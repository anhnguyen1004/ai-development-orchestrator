"""ExecutionPlan and ExecutionStage models with self-contained DAG invariant verification."""

import types
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from src.core.contracts.artifact import Sha256Hash
from src.core.contracts.ids import NonEmptyStr, StepId


class ExecutionStage(BaseModel):
    """A parallel execution wave where all steps have had their prerequisites satisfied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage_index: int = Field(ge=0)
    step_ids: tuple[StepId, ...] = Field(min_length=1)


class ExecutionPlan(BaseModel):
    """
    Pre-compiled, self-validating deterministic DAG execution plan.

    Immutability Guarantee:
    - topological_order is an immutable tuple.
    - execution_stages is an immutable tuple of ExecutionStage objects.
    - step_dependencies is an immutable mappingproxy of StepId -> tuple[StepId, ...].
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    workflow_id: NonEmptyStr
    workflow_version: NonEmptyStr
    definition_hash: Sha256Hash

    topological_order: tuple[StepId, ...] = Field(min_length=1)
    execution_stages: tuple[ExecutionStage, ...] = Field(min_length=1)
    step_dependencies: Mapping[StepId, tuple[StepId, ...]] = Field(
        default_factory=lambda: types.MappingProxyType({})
    )
    total_steps: int = Field(ge=1)

    @field_validator("step_dependencies", mode="after")
    @classmethod
    def freeze_and_normalize_dependencies(cls, v: Any) -> Any:
        if isinstance(v, (dict, types.MappingProxyType)):
            frozen: dict[StepId, tuple[StepId, ...]] = {
                StepId(str(k)): tuple(StepId(str(dep)) for dep in deps)
                for k, deps in v.items()
            }
            return types.MappingProxyType(frozen)
        raise ValueError("step_dependencies must be a mapping of StepId -> tuple[StepId, ...]")

    @field_serializer("step_dependencies")
    def serialize_step_dependencies(self, v: Any) -> dict[str, list[str]]:
        return {str(k): [str(d) for d in deps] for k, deps in v.items()}

    @model_validator(mode="after")
    def validate_plan_integrity(self) -> "ExecutionPlan":
        # 1. Step count and uniqueness in topological_order
        if len(self.topological_order) != len(set(self.topological_order)):
            raise ValueError("Duplicate steps detected in topological_order")
        if self.total_steps != len(self.topological_order):
            raise ValueError(
                f"total_steps ({self.total_steps}) does not match len(topological_order) ({len(self.topological_order)})"
            )

        # 2. Contiguous stage indexing starting at 0
        expected_indices = tuple(range(len(self.execution_stages)))
        actual_indices = tuple(s.stage_index for s in self.execution_stages)
        if actual_indices != expected_indices:
            raise ValueError(f"Stage indices must be contiguous starting from 0. Got: {actual_indices}")

        # 3. Exact ordering consistency: flatten(execution_stages) == topological_order
        flattened_stages = tuple(sid for stage in self.execution_stages for sid in stage.step_ids)
        if flattened_stages != self.topological_order:
            raise ValueError(
                f"Flattened execution_stages {flattened_stages} does not match topological_order {self.topological_order}"
            )

        # 4. Deterministic alphabetical sorting within each stage
        step_to_stage: dict[StepId, int] = {}
        for stage in self.execution_stages:
            if list(stage.step_ids) != sorted(stage.step_ids):
                raise ValueError(
                    f"Steps in stage {stage.stage_index} are not sorted deterministically: {stage.step_ids}"
                )
            for sid in stage.step_ids:
                step_to_stage[sid] = stage.stage_index

        # 5. Dependency mapping completeness:
        # set(step_dependencies.keys()) must exactly equal set(topological_order)
        topo_set = set(self.topological_order)
        if set(self.step_dependencies.keys()) != topo_set:
            raise ValueError(
                "step_dependencies keys must exactly match the set of step IDs in topological_order"
            )

        # 6. Precedence and stage invariants
        topo_index_map = {sid: idx for idx, sid in enumerate(self.topological_order)}

        for step_id, deps in self.step_dependencies.items():
            # No duplicate dependencies
            if len(deps) != len(set(deps)):
                raise ValueError(f"Step '{step_id}' has duplicate dependencies: {deps}")

            # No self-dependencies
            if step_id in deps:
                raise ValueError(f"Step '{step_id}' cannot depend on itself")

            step_stage = step_to_stage[step_id]
            step_pos = topo_index_map[step_id]

            for dep in deps:
                # Every dependency must reference an existing step
                if dep not in topo_set:
                    raise ValueError(f"Step '{step_id}' references unknown dependency '{dep}'")

                dep_stage = step_to_stage[dep]
                dep_pos = topo_index_map[dep]

                # Dependency must appear strictly earlier in topological_order
                if dep_pos >= step_pos:
                    raise ValueError(
                        f"Step '{step_id}' (pos {step_pos}) depends on '{dep}' (pos {dep_pos}), "
                        "which does not appear earlier in topological_order"
                    )

                # Dependency must belong to a strictly earlier execution stage
                if dep_stage >= step_stage:
                    raise ValueError(
                        f"Step '{step_id}' (stage {step_stage}) depends on '{dep}' (stage {dep_stage}), "
                        "which is not in a strictly earlier execution stage"
                    )

        return self
