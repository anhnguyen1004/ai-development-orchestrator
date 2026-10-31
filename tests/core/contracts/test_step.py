"""Unit tests for StepResult consistency validation and immutability semantics."""

import pytest
from pydantic import ValidationError

from src.core.contracts.approval import ApprovalRequest
from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.errors import ErrorCategory, ExecutionError
from src.core.contracts.status import StepOutcome
from src.core.contracts.step import StepResult

VALID_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def create_sample_approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        approval_id="appr-1",
        run_id="run-1",
        step_id="step-1",
        artifact=ArtifactRef(artifact_id="art-1", version=1, content_hash=VALID_HASH),
        summary="Review needed",
    )


def create_sample_error() -> ExecutionError:
    return ExecutionError(
        category=ErrorCategory.PERMANENT,
        message="Compilation failure",
    )


# --- Outcome: SUCCEEDED ---

def test_succeeded_valid():
    res = StepResult(
        outcome=StepOutcome.SUCCEEDED,
        current_state={"files": ["main.py"], "count": 1},
    )
    assert res.outcome == StepOutcome.SUCCEEDED
    assert res.current_state == {"files": ["main.py"], "count": 1}
    assert res.error is None
    assert res.approval_request is None
    assert res.skip_reason is None


def test_succeeded_with_contradictory_fields_rejected():
    # Succeeded cannot have error
    with pytest.raises(ValidationError, match="SUCCEEDED step cannot contain an error"):
        StepResult(
            outcome=StepOutcome.SUCCEEDED,
            current_state={},
            error=create_sample_error(),
        )

    # Succeeded cannot have approval_request
    with pytest.raises(ValidationError, match="SUCCEEDED step cannot contain an approval_request"):
        StepResult(
            outcome=StepOutcome.SUCCEEDED,
            current_state={},
            approval_request=create_sample_approval_request(),
        )

    # Succeeded cannot have skip_reason
    with pytest.raises(ValidationError, match="SUCCEEDED step cannot contain a skip_reason"):
        StepResult(
            outcome=StepOutcome.SUCCEEDED,
            current_state={},
            skip_reason="Already done",
        )


# --- Outcome: FAILED ---

def test_failed_valid():
    err = create_sample_error()
    res = StepResult(
        outcome=StepOutcome.FAILED,
        current_state={"attempt": 1},
        error=err,
    )
    assert res.outcome == StepOutcome.FAILED
    assert res.error == err


def test_failed_without_error_rejected():
    with pytest.raises(ValidationError, match="FAILED step requires an error"):
        StepResult(
            outcome=StepOutcome.FAILED,
            current_state={},
        )


def test_failed_with_approval_or_skip_rejected():
    with pytest.raises(ValidationError, match="FAILED step cannot contain an approval_request"):
        StepResult(
            outcome=StepOutcome.FAILED,
            current_state={},
            error=create_sample_error(),
            approval_request=create_sample_approval_request(),
        )

    with pytest.raises(ValidationError, match="FAILED step cannot contain a skip_reason"):
        StepResult(
            outcome=StepOutcome.FAILED,
            current_state={},
            error=create_sample_error(),
            skip_reason="Skip this",
        )


# --- Outcome: SKIPPED ---

def test_skipped_valid():
    res = StepResult(
        outcome=StepOutcome.SKIPPED,
        current_state={"cached": True},
        skip_reason="Cache up-to-date",
    )
    assert res.outcome == StepOutcome.SKIPPED
    assert res.skip_reason == "Cache up-to-date"


def test_skipped_without_valid_reason_rejected():
    with pytest.raises(ValidationError, match="SKIPPED step requires a non-empty skip_reason"):
        StepResult(
            outcome=StepOutcome.SKIPPED,
            current_state={},
            skip_reason=None,
        )

    with pytest.raises(ValidationError, match="SKIPPED step requires a non-empty skip_reason"):
        StepResult(
            outcome=StepOutcome.SKIPPED,
            current_state={},
            skip_reason="    ",
        )


def test_skipped_with_error_or_approval_rejected():
    with pytest.raises(ValidationError, match="SKIPPED step cannot contain an error"):
        StepResult(
            outcome=StepOutcome.SKIPPED,
            current_state={},
            skip_reason="Skip",
            error=create_sample_error(),
        )

    with pytest.raises(ValidationError, match="SKIPPED step cannot contain an approval_request"):
        StepResult(
            outcome=StepOutcome.SKIPPED,
            current_state={},
            skip_reason="Skip",
            approval_request=create_sample_approval_request(),
        )


# --- Outcome: WAITING_FOR_APPROVAL ---

def test_waiting_for_approval_valid():
    req = create_sample_approval_request()
    res = StepResult(
        outcome=StepOutcome.WAITING_FOR_APPROVAL,
        current_state={"stage": "review"},
        approval_request=req,
    )
    assert res.outcome == StepOutcome.WAITING_FOR_APPROVAL
    assert res.approval_request == req


def test_waiting_for_approval_without_request_rejected():
    with pytest.raises(ValidationError, match="WAITING_FOR_APPROVAL step requires an approval_request"):
        StepResult(
            outcome=StepOutcome.WAITING_FOR_APPROVAL,
            current_state={},
        )


def test_waiting_for_approval_with_error_or_skip_rejected():
    req = create_sample_approval_request()
    with pytest.raises(ValidationError, match="WAITING_FOR_APPROVAL step cannot contain an error"):
        StepResult(
            outcome=StepOutcome.WAITING_FOR_APPROVAL,
            current_state={},
            approval_request=req,
            error=create_sample_error(),
        )

    with pytest.raises(ValidationError, match="WAITING_FOR_APPROVAL step cannot contain a skip_reason"):
        StepResult(
            outcome=StepOutcome.WAITING_FOR_APPROVAL,
            current_state={},
            approval_request=req,
            skip_reason="Skip",
        )


# --- Immutability & Documented Nested Dict Limitation ---

def test_top_level_immutability():
    res = StepResult(
        outcome=StepOutcome.SUCCEEDED,
        current_state={"status": "initial"},
    )
    with pytest.raises(ValidationError):
        res.outcome = StepOutcome.FAILED  # type: ignore[misc]

    with pytest.raises(ValidationError):
        res.current_state = {"status": "mutated"}  # type: ignore[misc]


def test_documented_nested_dictionary_limitation():
    """
    Demonstrates the documented architectural limitation:
    frozen=True on BaseModel does not deeply freeze mutable dictionary values.
    Engine and applications must follow Copy-on-Write convention.
    """
    initial_dict = {"items": [1, 2, 3]}
    res = StepResult(
        outcome=StepOutcome.SUCCEEDED,
        current_state=initial_dict,
    )

    # In Python, nested dict references are still mutable
    res.current_state["items"].append(4)
    assert res.current_state["items"] == [1, 2, 3, 4]

