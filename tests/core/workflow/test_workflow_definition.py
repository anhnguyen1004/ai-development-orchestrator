"""Unit tests for WorkflowDefinition, WorkflowInputDefinition, and canonical hashing."""

import pytest
from pydantic import ValidationError

from src.core.workflow.definition import WorkflowDefinition, WorkflowInputDefinition
from src.core.workflow.step_definition import StepDefinition


def make_dummy_step(step_id: str, depends_on: tuple = (), **kwargs) -> StepDefinition:
    return StepDefinition(
        step_id=step_id,
        name=f"Name {step_id}",
        capability="code.implementation",
        depends_on=depends_on,
        **kwargs,
    )


# --- SemVer 2.0.0 Tests ---

@pytest.mark.parametrize(
    "valid_ver",
    ["1.0.0", "0.1.0-alpha.1", "1.0.0+build.123", "2.10.5-rc.1+20261031"],
)
def test_valid_semver(valid_ver: str):
    wf = WorkflowDefinition(
        workflow_id="wf_1",
        name="Workflow 1",
        version=valid_ver,
        steps=(make_dummy_step("s1"),),
    )
    assert wf.version == valid_ver


@pytest.mark.parametrize(
    "invalid_ver",
    [
        " 1.0.0 ",      # Surrounding whitespace rejected
        " 1.0.0",       # Leading whitespace rejected
        "1.0.0 ",       # Trailing whitespace rejected
        "1.0.0\n",      # Trailing newline rejected
        "01.0.0",       # Leading zero in major
        "1.01.0",       # Leading zero in minor
        "1.0.01",       # Leading zero in patch
        "1.0.0-01",     # Leading zero in numeric prerelease
        "1.0",          # Incomplete
        "v1.0.0",       # Leading 'v'
        "latest",       # Non-SemVer string
    ],
)
def test_invalid_semver_rejected(invalid_ver: str):
    with pytest.raises(ValidationError, match="not a valid Semantic Version 2.0.0"):
        WorkflowDefinition(
            workflow_id="wf_1",
            name="Workflow 1",
            version=invalid_ver,
            steps=(make_dummy_step("s1"),),
        )


def test_strict_semver_whitespace_rejection():
    # "1.0.0" -> accepted
    wf = WorkflowDefinition(
        workflow_id="wf_1",
        name="Workflow 1",
        version="1.0.0",
        steps=(make_dummy_step("s1"),),
    )
    assert wf.version == "1.0.0"

    # "1.0.0\n" -> rejected
    with pytest.raises(ValidationError, match="not a valid Semantic Version 2.0.0"):
        WorkflowDefinition(
            workflow_id="wf_1",
            name="Workflow 1",
            version="1.0.0\n",
            steps=(make_dummy_step("s1"),),
        )

    # "1.0.0 " -> rejected
    with pytest.raises(ValidationError, match="not a valid Semantic Version 2.0.0"):
        WorkflowDefinition(
            workflow_id="wf_1",
            name="Workflow 1",
            version="1.0.0 ",
            steps=(make_dummy_step("s1"),),
        )

    # " 1.0.0" -> rejected
    with pytest.raises(ValidationError, match="not a valid Semantic Version 2.0.0"):
        WorkflowDefinition(
            workflow_id="wf_1",
            name="Workflow 1",
            version=" 1.0.0",
            steps=(make_dummy_step("s1"),),
        )


# --- Input Key & Step ID Uniqueness ---

def test_reject_duplicate_workflow_input_keys():
    with pytest.raises(ValidationError, match="inputs contain duplicate keys"):
        WorkflowDefinition(
            workflow_id="wf_1",
            name="Workflow 1",
            version="1.0.0",
            inputs=(
                WorkflowInputDefinition(key="repo_url"),
                WorkflowInputDefinition(key="repo_url"),
            ),
            steps=(make_dummy_step("s1"),),
        )


def test_reject_duplicate_step_ids():
    with pytest.raises(ValidationError, match="steps contain duplicate step_ids"):
        WorkflowDefinition(
            workflow_id="wf_1",
            name="Workflow 1",
            version="1.0.0",
            steps=(
                make_dummy_step("dup_step"),
                make_dummy_step("dup_step"),
            ),
        )


# --- Deep Immutability of Metadata ---

def test_metadata_deep_immutability():
    wf = WorkflowDefinition(
        workflow_id="wf_1",
        name="Workflow 1",
        version="1.0.0",
        steps=(make_dummy_step("s1"),),
        metadata={"author": "team_a", "tags": {"env": "prod"}},
    )
    with pytest.raises(TypeError):
        wf.metadata["author"] = "team_b"  # type: ignore[index]

    with pytest.raises(TypeError):
        wf.metadata["tags"]["env"] = "staging"  # type: ignore[index]


# --- Canonical Hashing Invariance & Sensitivity ---

def test_hash_invariance_to_step_declaration_order():
    s1 = make_dummy_step("step_1", depends_on=("step_0",), inputs=("k1", "k2"))
    s2 = make_dummy_step("step_2", depends_on=("step_1",))

    wf_order_a = WorkflowDefinition(
        workflow_id="wf_test",
        name="Test WF",
        version="1.0.0",
        steps=(s1, s2),
    )
    wf_order_b = WorkflowDefinition(
        workflow_id="wf_test",
        name="Test WF",
        version="1.0.0",
        steps=(s2, s1),  # Swapped declaration order
    )

    assert wf_order_a.compute_hash() == wf_order_b.compute_hash()


def test_hash_invariance_to_dependency_order():
    s_a = make_dummy_step("step_c", depends_on=("step_b", "step_a"))
    s_b = make_dummy_step("step_c", depends_on=("step_a", "step_b"))

    wf_a = WorkflowDefinition(
        workflow_id="wf_test", name="Test WF", version="1.0.0", steps=(s_a,)
    )
    wf_b = WorkflowDefinition(
        workflow_id="wf_test", name="Test WF", version="1.0.0", steps=(s_b,)
    )

    assert wf_a.compute_hash() == wf_b.compute_hash()


@pytest.mark.parametrize(
    "modified_kwargs",
    [
        {"name": "Modified Name"},
        {"description": "New description"},
        {"version": "1.0.1"},
        {"state_schema": "CustomState.v2"},
        {"metadata": {"new_key": "val"}},
        {"inputs": (WorkflowInputDefinition(key="repo_url"),)},
    ],
)
def test_hash_sensitivity_workflow_fields(modified_kwargs: dict):
    base_wf = WorkflowDefinition(
        workflow_id="wf_base",
        name="Base Name",
        version="1.0.0",
        steps=(make_dummy_step("s1"),),
    )
    base_hash = base_wf.compute_hash()

    kwargs = {
        "workflow_id": "wf_base",
        "name": "Base Name",
        "version": "1.0.0",
        "steps": (make_dummy_step("s1"),),
        **modified_kwargs,
    }
    modified_wf = WorkflowDefinition(**kwargs)
    assert modified_wf.compute_hash() != base_hash


def test_hash_sensitivity_step_fields():
    base_wf = WorkflowDefinition(
        workflow_id="wf_base",
        name="Base Name",
        version="1.0.0",
        steps=(make_dummy_step("s1", description="Initial description"),),
    )
    mod_wf = WorkflowDefinition(
        workflow_id="wf_base",
        name="Base Name",
        version="1.0.0",
        steps=(make_dummy_step("s1", description="Changed description"),),
    )
    assert base_wf.compute_hash() != mod_wf.compute_hash()

