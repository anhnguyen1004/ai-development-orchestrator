"""Unit tests for WorkflowCompiler DAG verification, cycle detection, and Kahn compilation."""

import random
import pytest

from src.core.contracts.ids import StepId
from src.core.workflow.compiler import WorkflowCompiler
from src.core.workflow.definition import WorkflowDefinition
from src.core.workflow.errors import (
    CircularDependencyError,
    DuplicateStepIdError,
    SelfDependencyError,
    UnknownDependencyError,
)
from src.core.workflow.step_definition import StepDefinition


def make_step(step_id: str, depends_on: tuple = (), **kwargs) -> StepDefinition:
    return StepDefinition(
        step_id=step_id,
        name=f"Step {step_id}",
        capability="code.implementation",
        depends_on=depends_on,
        **kwargs,
    )


def test_linear_pipeline_compilation():
    # A -> B -> C
    steps = (
        make_step("step_a"),
        make_step("step_b", depends_on=("step_a",)),
        make_step("step_c", depends_on=("step_b",)),
    )
    wf = WorkflowDefinition(workflow_id="wf_linear", name="Linear WF", version="1.0.0", steps=steps)
    plan = WorkflowCompiler.compile(wf)

    assert plan.topological_order == (StepId("step_a"), StepId("step_b"), StepId("step_c"))
    assert len(plan.execution_stages) == 3
    assert plan.execution_stages[0].step_ids == (StepId("step_a"),)
    assert plan.execution_stages[1].step_ids == (StepId("step_b"),)
    assert plan.execution_stages[2].step_ids == (StepId("step_c"),)


def test_diamond_dag_compilation():
    # A -> B, A -> C, B -> D, C -> D
    steps = (
        make_step("step_a"),
        make_step("step_b", depends_on=("step_a",)),
        make_step("step_c", depends_on=("step_a",)),
        make_step("step_d", depends_on=("step_b", "step_c")),
    )
    wf = WorkflowDefinition(workflow_id="wf_diamond", name="Diamond WF", version="1.0.0", steps=steps)
    plan = WorkflowCompiler.compile(wf)

    assert plan.topological_order == (
        StepId("step_a"),
        StepId("step_b"),
        StepId("step_c"),
        StepId("step_d"),
    )
    assert len(plan.execution_stages) == 3
    assert plan.execution_stages[0].step_ids == (StepId("step_a"),)
    # Stage 1 has both B and C, sorted alphabetically
    assert plan.execution_stages[1].step_ids == (StepId("step_b"), StepId("step_c"))
    assert plan.execution_stages[2].step_ids == (StepId("step_d"),)


def test_disconnected_subgraphs_tie_breaking():
    # Two independent parallel chains:
    # Chain 1: a1 -> a2
    # Chain 2: b1 -> b2
    steps = (
        make_step("b1"),
        make_step("a2", depends_on=("a1",)),
        make_step("b2", depends_on=("b1",)),
        make_step("a1"),
    )
    wf = WorkflowDefinition(workflow_id="wf_parallel", name="Parallel WF", version="1.0.0", steps=steps)
    plan = WorkflowCompiler.compile(wf)

    # Stage 0: a1, b1 (sorted alphabetically)
    # Stage 1: a2, b2 (sorted alphabetically)
    assert plan.execution_stages[0].step_ids == (StepId("a1"), StepId("b1"))
    assert plan.execution_stages[1].step_ids == (StepId("a2"), StepId("b2"))
    assert plan.topological_order == (StepId("a1"), StepId("b1"), StepId("a2"), StepId("b2"))


def test_compiler_determinism_under_step_shuffling():
    # Construct a non-trivial DAG
    steps = [
        make_step("root"),
        make_step("b_branch", depends_on=("root",)),
        make_step("a_branch", depends_on=("root",)),
        make_step("c_branch", depends_on=("root",)),
        make_step("join", depends_on=("a_branch", "b_branch", "c_branch")),
    ]
    wf_base = WorkflowDefinition(workflow_id="wf_det", name="Deterministic WF", version="1.0.0", steps=tuple(steps))
    base_plan = WorkflowCompiler.compile(wf_base)

    # Shuffle definition step order 20 times; plan must be identical every time
    for _ in range(20):
        shuffled = steps.copy()
        random.shuffle(shuffled)
        wf_shuffled = WorkflowDefinition(
            workflow_id="wf_det", name="Deterministic WF", version="1.0.0", steps=tuple(shuffled)
        )
        plan_shuffled = WorkflowCompiler.compile(wf_shuffled)

        assert plan_shuffled.topological_order == base_plan.topological_order
        assert plan_shuffled.execution_stages == base_plan.execution_stages
        assert plan_shuffled.definition_hash == base_plan.definition_hash


def test_cycle_detection_error_path():
    # Cycle: a -> b -> c -> a
    steps = (
        make_step("step_a", depends_on=("step_c",)),
        make_step("step_b", depends_on=("step_a",)),
        make_step("step_c", depends_on=("step_b",)),
    )
    wf = WorkflowDefinition(workflow_id="wf_cycle", name="Cycle WF", version="1.0.0", steps=steps)
    with pytest.raises(CircularDependencyError) as exc_info:
        WorkflowCompiler.compile(wf)

    err = exc_info.value
    assert len(err.cycle_path) >= 3
    # Check that formatted message mentions cycle path
    assert "Circular dependency cycle detected" in str(err)
    assert "->" in str(err)


def test_unknown_dependency_rejection():
    steps = (make_step("step_a", depends_on=("missing_step",)),)
    wf = WorkflowDefinition(workflow_id="wf_unknown", name="Unknown WF", version="1.0.0", steps=steps)
    with pytest.raises(UnknownDependencyError) as exc_info:
        WorkflowCompiler.compile(wf)

    assert exc_info.value.unknown_dependency == "missing_step"

