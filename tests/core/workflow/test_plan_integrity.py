"""Unit tests for ExecutionPlan structural invariants and immutability."""

import pytest
from pydantic import ValidationError

from src.core.contracts.artifact import Sha256Hash
from src.core.contracts.ids import StepId
from src.core.workflow.plan import ExecutionPlan, ExecutionStage

DUMMY_HASH = Sha256Hash("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


def test_valid_execution_plan_creation():
    plan = ExecutionPlan(
        workflow_id="wf_sample",
        workflow_version="1.0.0",
        definition_hash=DUMMY_HASH,
        topological_order=(StepId("s0"), StepId("s1"), StepId("s2")),
        execution_stages=(
            ExecutionStage(stage_index=0, step_ids=(StepId("s0"),)),
            ExecutionStage(stage_index=1, step_ids=(StepId("s1"), StepId("s2"))),
        ),
        step_dependencies={
            StepId("s0"): (),
            StepId("s1"): (StepId("s0"),),
            StepId("s2"): (StepId("s0"),),
        },
        total_steps=3,
    )
    assert plan.total_steps == 3
    assert plan.topological_order == (StepId("s0"), StepId("s1"), StepId("s2"))


def test_reject_duplicate_steps_in_topological_order():
    with pytest.raises(ValidationError, match="Duplicate steps detected in topological_order"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s0"), StepId("s0")),
            execution_stages=(ExecutionStage(stage_index=0, step_ids=(StepId("s0"),)),),
            step_dependencies={StepId("s0"): ()},
            total_steps=2,
        )


def test_reject_total_steps_mismatch():
    with pytest.raises(ValidationError, match="total_steps"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s0"),),
            execution_stages=(ExecutionStage(stage_index=0, step_ids=(StepId("s0"),)),),
            step_dependencies={StepId("s0"): ()},
            total_steps=5,  # Mismatch: 5 != 1
        )


def test_reject_non_contiguous_stage_indices():
    with pytest.raises(ValidationError, match="Stage indices must be contiguous starting from 0"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s0"), StepId("s1")),
            execution_stages=(
                ExecutionStage(stage_index=0, step_ids=(StepId("s0"),)),
                ExecutionStage(stage_index=2, step_ids=(StepId("s1"),)),  # Index 2 instead of 1
            ),
            step_dependencies={StepId("s0"): (), StepId("s1"): ()},
            total_steps=2,
        )


def test_reject_unsorted_steps_in_stage():
    with pytest.raises(ValidationError, match="not sorted deterministically"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s_beta"), StepId("s_alpha")),
            execution_stages=(
                ExecutionStage(stage_index=0, step_ids=(StepId("s_beta"), StepId("s_alpha"))),  # Unsorted
            ),
            step_dependencies={StepId("s_beta"): (), StepId("s_alpha"): ()},
            total_steps=2,
        )


def test_reject_flattened_stages_not_matching_topological_order():
    # Set of steps is identical, but the stage grouping order violates the topological order
    with pytest.raises(ValidationError, match="Flattened execution_stages"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s1"), StepId("s2")),
            execution_stages=(
                ExecutionStage(stage_index=0, step_ids=(StepId("s2"),)),
                ExecutionStage(stage_index=1, step_ids=(StepId("s1"),)),
            ),
            step_dependencies={StepId("s1"): (), StepId("s2"): ()},
            total_steps=2,
        )


def test_reject_step_dependencies_keys_mismatch():
    with pytest.raises(ValidationError, match="step_dependencies keys must exactly match"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s0"), StepId("s1")),
            execution_stages=(
                ExecutionStage(stage_index=0, step_ids=(StepId("s0"), StepId("s1"))),
            ),
            step_dependencies={StepId("s0"): ()},  # Missing 's1'
            total_steps=2,
        )


def test_reject_dependency_not_in_strictly_earlier_stage():
    # s1 depends on s0, but both are in Stage 0
    with pytest.raises(ValidationError, match="not in a strictly earlier execution stage"):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s0"), StepId("s1")),
            execution_stages=(
                ExecutionStage(stage_index=0, step_ids=(StepId("s0"), StepId("s1"))),
            ),
            step_dependencies={
                StepId("s0"): (),
                StepId("s1"): (StepId("s0"),),
            },
            total_steps=2,
        )


def test_reject_dependency_not_earlier_in_topological_order():
    # s0 depends on s1, but s0 appears first in topological_order
    with pytest.raises(ValidationError):
        ExecutionPlan(
            workflow_id="wf_test",
            workflow_version="1.0.0",
            definition_hash=DUMMY_HASH,
            topological_order=(StepId("s0"), StepId("s1")),
            execution_stages=(
                ExecutionStage(stage_index=0, step_ids=(StepId("s0"),)),
                ExecutionStage(stage_index=1, step_ids=(StepId("s1"),)),
            ),
            step_dependencies={
                StepId("s0"): (StepId("s1"),),
                StepId("s1"): (),
            },
            total_steps=2,
        )


def test_step_dependencies_deep_immutability():
    plan = ExecutionPlan(
        workflow_id="wf_sample",
        workflow_version="1.0.0",
        definition_hash=DUMMY_HASH,
        topological_order=(StepId("s0"),),
        execution_stages=(ExecutionStage(stage_index=0, step_ids=(StepId("s0"),)),),
        step_dependencies={StepId("s0"): ()},
        total_steps=1,
    )

    # Top-level mapping mutation rejected
    with pytest.raises(TypeError):
        plan.step_dependencies[StepId("s0")] = (StepId("s_new"),)  # type: ignore[index]

    with pytest.raises(TypeError):
        del plan.step_dependencies[StepId("s0")]  # type: ignore[misc]

    # Nested tuple mutation rejected
    with pytest.raises(AttributeError):
        plan.step_dependencies[StepId("s0")].append("s_new")  # type: ignore[attr-defined]

