"""Deterministic DAG compiler and workflow integrity validator."""

from collections import defaultdict
from src.core.contracts.ids import StepId
from src.core.workflow.definition import WorkflowDefinition
from src.core.workflow.errors import (
    CircularDependencyError,
    DuplicateOutputProducerError,
    DuplicateStepIdError,
    OptionalInputAsRequiredDependencyError,
    OutputOverwritesInputError,
    SelfDependencyError,
    UnknownDependencyError,
    UnsatisfiedStateInputError,
)
from src.core.workflow.plan import ExecutionPlan, ExecutionStage
from src.core.workflow.step_definition import StepDefinition


class WorkflowCompiler:
    """Validates WorkflowDefinition integrity and compiles deterministic ExecutionPlan."""

    @classmethod
    def compile(cls, defn: WorkflowDefinition) -> ExecutionPlan:
        """
        Validate workflow declarative contracts and compile into an ExecutionPlan.

        Validation Steps:
        1. Check duplicate step IDs.
        2. Check for self-dependencies and unknown dependencies.
        3. Detect cycles in the DAG with formatted cycle path reporting.
        4. Validate single output producer across workflow.
        5. Validate step outputs do not overwrite workflow inputs.
        6. Validate state input availability (required workflow inputs or ancestor outputs).
        7. Enforce optional workflow input restriction.
        8. Execute Kahn's algorithm with lexicographical tie-breaking to produce
           deterministic topological order and execution stages.
        """
        step_list = defn.steps
        step_ids = [s.step_id for s in step_list]

        # 1. Duplicate step IDs
        if len(step_ids) != len(set(step_ids)):
            seen = set()
            for sid in step_ids:
                if sid in seen:
                    raise DuplicateStepIdError(sid)
                seen.add(sid)

        step_map: dict[StepId, StepDefinition] = {s.step_id: s for s in step_list}

        # 2. Self and Unknown dependencies
        for step in step_list:
            if step.step_id in step.depends_on:
                raise SelfDependencyError(step.step_id)
            for dep in step.depends_on:
                if dep not in step_map:
                    raise UnknownDependencyError(step.step_id, dep)

        # 3. Cycle Detection
        cycle_path = cls._find_cycle(step_ids, step_map)
        if cycle_path is not None:
            raise CircularDependencyError(cycle_path)

        # 4 & 5 & 6 & 7: Data Contract Validation
        cls._validate_data_contracts(defn, step_map)

        # 8. Deterministic Kahn's Algorithm with Stage Grouping
        topological_order, execution_stages = cls._compute_deterministic_plan(step_list, step_map)

        # Build self-contained dependency mapping
        step_dependencies: dict[StepId, tuple[StepId, ...]] = {
            s.step_id: tuple(sorted(s.depends_on)) for s in step_list
        }

        # Compile definition hash
        definition_hash = defn.compute_hash()

        return ExecutionPlan(
            workflow_id=defn.workflow_id,
            workflow_version=defn.version,
            definition_hash=definition_hash,
            topological_order=tuple(topological_order),
            execution_stages=tuple(execution_stages),
            step_dependencies=step_dependencies,
            total_steps=len(topological_order),
        )

    @classmethod
    def _find_cycle(
        cls, step_ids: list[StepId], step_map: dict[StepId, StepDefinition]
    ) -> list[StepId] | None:
        """Find a cycle in dependency graph using DFS with coloring (0=WHITE, 1=GRAY, 2=BLACK)."""
        color: dict[StepId, int] = {sid: 0 for sid in step_ids}
        path: list[StepId] = []

        def dfs(node: StepId) -> list[StepId] | None:
            color[node] = 1  # GRAY
            path.append(node)

            for dep in sorted(step_map[node].depends_on):
                if dep not in step_map:
                    continue
                if color[dep] == 1:
                    # Found a cycle back to a GRAY node
                    cycle_start_idx = path.index(dep)
                    return path[cycle_start_idx:] + [dep]
                if color[dep] == 0:
                    found = dfs(dep)
                    if found is not None:
                        return found

            path.pop()
            color[node] = 2  # BLACK
            return None

        for sid in sorted(step_ids):
            if color[sid] == 0:
                cycle = dfs(sid)
                if cycle is not None:
                    return cycle

        return None

    @classmethod
    def _validate_data_contracts(
        cls, defn: WorkflowDefinition, step_map: dict[StepId, StepDefinition]
    ) -> None:
        """Validate input/output contracts, single producer rule, and ancestor visibility."""
        req_wf_inputs = {inp.key for inp in defn.inputs if inp.required}
        opt_wf_inputs = {inp.key for inp in defn.inputs if not inp.required}
        all_wf_inputs = req_wf_inputs | opt_wf_inputs

        # Check Single Producer rule and Workflow Input Collision
        output_to_step: dict[str, StepId] = {}
        for step in defn.steps:
            for out_key in step.outputs:
                # Rule: Step output cannot overwrite workflow input
                if out_key in all_wf_inputs:
                    raise OutputOverwritesInputError(out_key, step.step_id)

                # Rule: Single producer across all steps
                if out_key in output_to_step:
                    raise DuplicateOutputProducerError(
                        out_key, (output_to_step[out_key], step.step_id)
                    )
                output_to_step[out_key] = step.step_id

        # Precompute ancestors for all steps
        ancestors_map: dict[StepId, set[StepId]] = {
            step.step_id: cls._get_ancestors(step.step_id, step_map)
            for step in defn.steps
        }

        # Check Step Inputs
        for step in defn.steps:
            ancestors = ancestors_map[step.step_id]

            for in_key in step.inputs:
                # Rule: Step cannot consume an optional workflow input in MVP
                if in_key in opt_wf_inputs:
                    raise OptionalInputAsRequiredDependencyError(step.step_id, in_key)

                # Satisfied if present in required workflow inputs
                if in_key in req_wf_inputs:
                    continue

                # Satisfied if produced by an ancestor step
                producer = output_to_step.get(in_key)
                if producer is not None and producer in ancestors:
                    continue

                # Determine detailed failure reason for clear error reporting
                if producer is None:
                    reason = f"key '{in_key}' is not produced by any workflow input or step"
                elif step.step_id in ancestors_map.get(producer, set()):
                    reason = f"key '{in_key}' is produced by downstream step '{producer}'"
                else:
                    reason = f"key '{in_key}' is produced by concurrent/non-ancestor step '{producer}' (missing dependency)"

                raise UnsatisfiedStateInputError(step.step_id, in_key, reason)

    @classmethod
    def _get_ancestors(
        cls, step_id: StepId, step_map: dict[StepId, StepDefinition]
    ) -> set[StepId]:
        """Compute all transitive prerequisite ancestors for a step."""
        ancestors: set[StepId] = set()
        queue = list(step_map[step_id].depends_on)
        while queue:
            curr = queue.pop(0)
            if curr not in ancestors:
                ancestors.add(curr)
                if curr in step_map:
                    queue.extend(step_map[curr].depends_on)
        return ancestors

    @classmethod
    def _compute_deterministic_plan(
        cls,
        step_list: tuple[StepDefinition, ...],
        step_map: dict[StepId, StepDefinition],
    ) -> tuple[list[StepId], list[ExecutionStage]]:
        """
        Execute Kahn's algorithm with lexicographical tie-breaking.
        Computes deterministic topological_order and parallel execution_stages.
        """
        # in-degree represents number of prerequisites pending
        in_degree: dict[StepId, int] = {s.step_id: len(s.depends_on) for s in step_list}

        # forward graph: prereq -> list of dependent steps
        forward_graph: dict[StepId, list[StepId]] = defaultdict(list)
        for s in step_list:
            for dep in s.depends_on:
                forward_graph[dep].append(s.step_id)

        # Stage 0: All nodes with in-degree == 0, sorted alphabetically
        current_wave = sorted([sid for sid, deg in in_degree.items() if deg == 0])

        topological_order: list[StepId] = []
        execution_stages: list[ExecutionStage] = []
        stage_idx = 0

        while current_wave:
            # Record current stage wave
            execution_stages.append(
                ExecutionStage(stage_index=stage_idx, step_ids=tuple(current_wave))
            )
            topological_order.extend(current_wave)
            stage_idx += 1

            next_wave_set: set[StepId] = set()
            for completed_node in current_wave:
                for dependent_node in forward_graph[completed_node]:
                    in_degree[dependent_node] -= 1
                    if in_degree[dependent_node] == 0:
                        next_wave_set.add(dependent_node)

            # Sort next wave deterministically
            current_wave = sorted(next_wave_set)

        return topological_order, execution_stages

