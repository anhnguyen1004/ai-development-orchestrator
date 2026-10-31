"""Unit tests for workflow data contracts, single producer rule, and input visibility."""

import pytest

from src.core.workflow.compiler import WorkflowCompiler
from src.core.workflow.definition import WorkflowDefinition, WorkflowInputDefinition
from src.core.workflow.errors import (
    DuplicateOutputProducerError,
    OptionalInputAsRequiredDependencyError,
    OutputOverwritesInputError,
    UnsatisfiedStateInputError,
)
from src.core.workflow.step_definition import StepDefinition


def make_step(step_id: str, depends_on: tuple = (), inputs: tuple = (), outputs: tuple = ()) -> StepDefinition:
    return StepDefinition(
        step_id=step_id,
        name=f"Step {step_id}",
        capability="code.implementation",
        depends_on=depends_on,
        inputs=inputs,
        outputs=outputs,
    )


# --- Single Producer Rule ---

def test_duplicate_output_producer_rejected():
    steps = (
        make_step("step_1", outputs=("code_patch",)),
        make_step("step_2", outputs=("code_patch",)),  # Collision
    )
    wf = WorkflowDefinition(workflow_id="wf_dup_out", name="Dup Output WF", version="1.0.0", steps=steps)
    with pytest.raises(DuplicateOutputProducerError) as exc_info:
        WorkflowCompiler.compile(wf)

    err = exc_info.value
    assert err.output_key == "code_patch"
    assert "step_1" in err.step_ids
    assert "step_2" in err.step_ids


# --- Workflow Input Collision Rule ---

def test_step_output_overwriting_required_input_rejected():
    wf = WorkflowDefinition(
        workflow_id="wf_overwrite_req",
        name="Overwrite Req WF",
        version="1.0.0",
        inputs=(WorkflowInputDefinition(key="repo_url", required=True),),
        steps=(make_step("step_1", outputs=("repo_url",)),),  # Collides with input
    )
    with pytest.raises(OutputOverwritesInputError) as exc_info:
        WorkflowCompiler.compile(wf)

    assert exc_info.value.key == "repo_url"
    assert exc_info.value.step_id == "step_1"


def test_step_output_overwriting_optional_input_rejected():
    wf = WorkflowDefinition(
        workflow_id="wf_overwrite_opt",
        name="Overwrite Opt WF",
        version="1.0.0",
        inputs=(WorkflowInputDefinition(key="auth_token", required=False),),
        steps=(make_step("step_1", outputs=("auth_token",)),),
    )
    with pytest.raises(OutputOverwritesInputError) as exc_info:
        WorkflowCompiler.compile(wf)

    assert exc_info.value.key == "auth_token"


# --- Optional Input Semantics (4 Cases) ---

def test_case_1_required_workflow_input_consumed_valid():
    wf = WorkflowDefinition(
        workflow_id="wf_case_1",
        name="Case 1 WF",
        version="1.0.0",
        inputs=(WorkflowInputDefinition(key="repo_url", required=True),),
        steps=(make_step("step_1", inputs=("repo_url",)),),
    )
    plan = WorkflowCompiler.compile(wf)
    assert plan.total_steps == 1


def test_case_2_optional_workflow_input_consumed_raises_specific_error():
    wf = WorkflowDefinition(
        workflow_id="wf_case_2",
        name="Case 2 WF",
        version="1.0.0",
        inputs=(WorkflowInputDefinition(key="auth_token", required=False),),
        steps=(make_step("step_1", inputs=("auth_token",)),),
    )
    # Must raise OptionalInputAsRequiredDependencyError, NOT UnsatisfiedStateInputError
    with pytest.raises(OptionalInputAsRequiredDependencyError) as exc_info:
        WorkflowCompiler.compile(wf)

    assert exc_info.value.key == "auth_token"
    assert exc_info.value.step_id == "step_1"


def test_case_3_optional_workflow_input_unused_valid():
    wf = WorkflowDefinition(
        workflow_id="wf_case_3",
        name="Case 3 WF",
        version="1.0.0",
        inputs=(
            WorkflowInputDefinition(key="repo_url", required=True),
            WorkflowInputDefinition(key="auth_token", required=False),  # Unused optional input
        ),
        steps=(make_step("step_1", inputs=("repo_url",)),),
    )
    plan = WorkflowCompiler.compile(wf)
    assert plan.total_steps == 1


def test_case_4_required_workflow_input_unused_valid():
    wf = WorkflowDefinition(
        workflow_id="wf_case_4",
        name="Case 4 WF",
        version="1.0.0",
        inputs=(
            WorkflowInputDefinition(key="repo_url", required=True),
            WorkflowInputDefinition(key="unused_flag", required=True),  # Unused required input
        ),
        steps=(make_step("step_1", inputs=("repo_url",)),),
    )
    plan = WorkflowCompiler.compile(wf)
    assert plan.total_steps == 1


# --- Ancestor Output Visibility Rule ---

def test_ancestor_output_visibility_valid():
    # Step A produces 'spec' -> Step B (depends on A) consumes 'spec' and produces 'code'
    # -> Step C (depends on B) consumes 'spec' (from ancestor A) and 'code' (from ancestor B)
    steps = (
        make_step("step_a", outputs=("spec",)),
        make_step("step_b", depends_on=("step_a",), inputs=("spec",), outputs=("code",)),
        make_step("step_c", depends_on=("step_b",), inputs=("spec", "code")),
    )
    wf = WorkflowDefinition(workflow_id="wf_ancestor_valid", name="Ancestor Valid WF", version="1.0.0", steps=steps)
    plan = WorkflowCompiler.compile(wf)
    assert plan.total_steps == 3


def test_downstream_output_invisible_rejected():
    # Step A attempts to consume 'code' which is only produced downstream by Step B
    steps = (
        make_step("step_a", inputs=("code",)),
        make_step("step_b", depends_on=("step_a",), outputs=("code",)),
    )
    wf = WorkflowDefinition(workflow_id="wf_downstream_inv", name="Downstream Inv WF", version="1.0.0", steps=steps)
    with pytest.raises(UnsatisfiedStateInputError) as exc_info:
        WorkflowCompiler.compile(wf)

    assert "downstream step" in str(exc_info.value)


def test_concurrent_output_invisible_in_diamond_dag():
    # Diamond:
    #      Root
    #     /    \
    # Branch1  Branch2 (Branch2 outputs 'metrics')
    #     \    /
    #      Join
    # Branch1 tries to consume 'metrics' produced by parallel Branch2
    steps = (
        make_step("root"),
        make_step("branch_1", depends_on=("root",), inputs=("metrics",)),  # Concurrent consumer
        make_step("branch_2", depends_on=("root",), outputs=("metrics",)), # Producer in other branch
        make_step("join", depends_on=("branch_1", "branch_2")),
    )
    wf = WorkflowDefinition(workflow_id="wf_concurrent", name="Concurrent WF", version="1.0.0", steps=steps)
    with pytest.raises(UnsatisfiedStateInputError) as exc_info:
        WorkflowCompiler.compile(wf)

    assert "missing dependency" in str(exc_info.value)

