"""Unit tests for StateManager Copy-on-Write validation and output contract enforcement."""

import math
import pytest

from src.core.contracts.approval import ApprovalRequest
from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.errors import ErrorCategory, ExecutionError
from src.core.contracts.ids import ApprovalId, ArtifactId, NonEmptyStr, RunId, StepId
from src.core.contracts.status import ApprovalStatus, StepOutcome
from src.core.contracts.step import StepResult
from src.core.engine.errors import ApprovalContractError, StateValidationError
from src.core.engine.state_manager import StateManager
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import Capability, StepDefinition

DUMMY_HASH = "a" * 64


def create_step_def(
    step_id: str = "step-1",
    outputs: tuple[str, ...] = ("out_key",),
) -> StepDefinition:
    return StepDefinition(
        step_id=StepId(step_id),
        name=NonEmptyStr(f"Name {step_id}"),
        capability=Capability("capability.test"),
        outputs=tuple(NonEmptyStr(o) for o in outputs),
    )


class TestStateManagerValidateAndCommit:
    def test_succeeded_step_commits_exact_outputs(self):
        pre_state = WorkflowState[dict[str, object]](
            schema_id="test.schema",
            data={"existing_key": "val1", "num": 10},
        )
        step_def = create_step_def(outputs=("output_a", "output_b"))

        # Complete snapshot with pre-existing keys and exact new outputs
        returned_snapshot = {
            "existing_key": "val1",
            "num": 10,
            "output_a": "result_a",
            "output_b": {"nested": True},
        }
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=returned_snapshot,
        )

        committed_state = StateManager.validate_and_commit(pre_state, result, step_def)
        assert committed_state.schema_id == "test.schema"
        assert committed_state.data["existing_key"] == "val1"
        assert committed_state.data["num"] == 10
        assert committed_state.data["output_a"] == "result_a"
        assert committed_state.data["output_b"] == {"nested": True}

        # Verify pre_state was not mutated in place
        assert "output_a" not in pre_state.data

    def test_rejects_missing_declared_output(self):
        pre_state = WorkflowState[dict[str, object]](data={"base": 1})
        step_def = create_step_def(outputs=("out_1", "out_2"))

        # Returns only out_1; out_2 is missing
        returned = {"base": 1, "out_1": "present"}
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=returned,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "failed to produce declared output key(s): ['out_2']" in str(exc_info.value)
        assert "out_1" not in pre_state.data

    def test_rejects_undeclared_new_output(self):
        pre_state = WorkflowState[dict[str, object]](data={"base": 1})
        step_def = create_step_def(outputs=("out_1",))

        # Returns out_1 and undeclared out_rogue
        returned = {"base": 1, "out_1": "ok", "out_rogue": "illegal"}
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=returned,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "produced undeclared output key(s): ['out_rogue']" in str(exc_info.value)
        assert "out_1" not in pre_state.data

    def test_rejects_deletion_of_existing_key(self):
        pre_state = WorkflowState[dict[str, object]](data={"key_a": 1, "key_b": 2})
        step_def = create_step_def(outputs=("out_key",))

        # Omitted key_b
        returned = {"key_a": 1, "out_key": "ok"}
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=returned,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "illegally deleted existing state key 'key_b'" in str(exc_info.value)

    def test_rejects_modification_of_existing_key(self):
        pre_state = WorkflowState[dict[str, object]](data={"key_a": "original", "num": 42})
        step_def = create_step_def(outputs=("out_key",))

        # Mutated key_a
        returned = {"key_a": "MUTATED", "num": 42, "out_key": "ok"}
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=returned,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "illegally modified existing state key 'key_a'" in str(exc_info.value)
        assert pre_state.data["key_a"] == "original"

    def test_rejects_partial_patch_as_current_state(self):
        """Demonstrates that returning only the patch fails because pre-existing keys are missing."""
        pre_state = WorkflowState[dict[str, object]](data={"user_id": "usr-1", "org_id": "org-2"})
        step_def = create_step_def(outputs=("report",))

        # Returning only the patch violates complete snapshot contract
        partial_patch = {"report": "done"}
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=partial_patch,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "illegally deleted existing state key" in str(exc_info.value)

    def test_step_with_zero_outputs(self):
        pre_state = WorkflowState[dict[str, object]](data={"key": 1})
        step_def = create_step_def(outputs=())

        # Exact match (0 new outputs) succeeds
        result_ok = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state={"key": 1},
        )
        committed = StateManager.validate_and_commit(pre_state, result_ok, step_def)
        assert committed.data == {"key": 1}

        # Adding any new output fails
        result_bad = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state={"key": 1, "rogue": 2},
        )
        with pytest.raises(StateValidationError):
            StateManager.validate_and_commit(pre_state, result_bad, step_def)

    def test_rejects_non_json_serializable_output(self):
        pre_state = WorkflowState[dict[str, object]](data={"k": 1})
        step_def = create_step_def(outputs=("nan_key",))

        returned = {"k": 1, "nan_key": float("nan")}
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=returned,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "non-JSON serializable output" in str(exc_info.value)

    def test_skipped_outcome_preserves_pre_state(self):
        pre_state = WorkflowState[dict[str, object]](data={"init": 100})
        step_def = create_step_def()
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.SKIPPED,
            current_state={"init": 100, "ignored": 999},
            skip_reason="Prerequisite not met",
        )

        committed = StateManager.validate_and_commit(pre_state, result, step_def)
        assert committed == pre_state
        assert "ignored" not in committed.data

    def test_failed_outcome_quarantines_and_preserves_pre_state(self):
        pre_state = WorkflowState[dict[str, object]](data={"init": 100})
        step_def = create_step_def()
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.FAILED,
            current_state={"init": 100, "partial_corrupted": True},
            error=ExecutionError(
                category=ErrorCategory.TRANSIENT,
                message=NonEmptyStr("Network error"),
            ),
        )

        committed = StateManager.validate_and_commit(pre_state, result, step_def)
        assert committed == pre_state
        assert "partial_corrupted" not in committed.data

    def test_waiting_for_approval_valid_single_output_preserves_pre_state(self):
        pre_state = WorkflowState[dict[str, object]](data={"init": 100})
        step_def = create_step_def(outputs=("spec_artifact",))
        req = ApprovalRequest(
            approval_id=ApprovalId("app-1"),
            run_id=RunId("run-1"),
            step_id=StepId("step-1"),
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                version=1,
                content_hash=DUMMY_HASH,
            ),
            summary=NonEmptyStr("Review spec"),
            status=ApprovalStatus.PENDING,
        )
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.WAITING_FOR_APPROVAL,
            current_state={"init": 100},
            approval_request=req,
        )

        committed = StateManager.validate_and_commit(pre_state, result, step_def)
        # Pre-state is returned unchanged; candidate artifact not committed
        assert committed == pre_state
        assert "spec_artifact" not in committed.data

    def test_waiting_for_approval_zero_outputs_raises_approval_contract_error(self):
        pre_state = WorkflowState[dict[str, object]](data={"init": 100})
        step_def = create_step_def(outputs=())  # 0 outputs
        req = ApprovalRequest(
            approval_id=ApprovalId("app-1"),
            run_id=RunId("run-1"),
            step_id=StepId("step-1"),
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                version=1,
                content_hash=DUMMY_HASH,
            ),
            summary=NonEmptyStr("Review spec"),
        )
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.WAITING_FOR_APPROVAL,
            current_state={"init": 100},
            approval_request=req,
        )

        with pytest.raises(ApprovalContractError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "must declare exactly 1 output, got 0" in str(exc_info.value)

    def test_waiting_for_approval_multiple_outputs_raises_approval_contract_error(self):
        pre_state = WorkflowState[dict[str, object]](data={"init": 100})
        step_def = create_step_def(outputs=("art_1", "art_2"))  # 2 outputs
        req = ApprovalRequest(
            approval_id=ApprovalId("app-1"),
            run_id=RunId("run-1"),
            step_id=StepId("step-1"),
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                version=1,
                content_hash=DUMMY_HASH,
            ),
            summary=NonEmptyStr("Review spec"),
        )
        result = StepResult[dict[str, object]](
            outcome=StepOutcome.WAITING_FOR_APPROVAL,
            current_state={"init": 100},
            approval_request=req,
        )

        with pytest.raises(ApprovalContractError) as exc_info:
            StateManager.validate_and_commit(pre_state, result, step_def)

        assert "must declare exactly 1 output, got 2" in str(exc_info.value)


class TestStateManagerCommitApprovedArtifact:
    def test_commit_approved_artifact_success(self):
        pre_state = WorkflowState[dict[str, object]](data={"init": "data"})
        step_def = create_step_def(outputs=("approved_spec",))
        ref = ArtifactRef(
            artifact_id=ArtifactId("art-100"),
            version=2,
            content_hash=DUMMY_HASH,
            media_type="text/markdown",
        )

        new_state = StateManager.commit_approved_artifact(pre_state, step_def, ref)
        assert new_state.data["init"] == "data"
        assert new_state.data["approved_spec"] == {
            "artifact_id": "art-100",
            "version": 2,
            "content_hash": DUMMY_HASH,
            "media_type": "text/markdown",
            "uri": None,
        }

    def test_commit_approved_artifact_rejects_non_single_output(self):
        pre_state = WorkflowState[dict[str, object]](data={})
        step_def_0 = create_step_def(outputs=())
        step_def_2 = create_step_def(outputs=("a", "b"))
        ref = ArtifactRef(
            artifact_id=ArtifactId("art-100"),
            version=1,
            content_hash=DUMMY_HASH,
        )

        with pytest.raises(ApprovalContractError):
            StateManager.commit_approved_artifact(pre_state, step_def_0, ref)

        with pytest.raises(ApprovalContractError):
            StateManager.commit_approved_artifact(pre_state, step_def_2, ref)

    def test_commit_approved_artifact_rejects_existing_output_key_collision(self):
        pre_state = WorkflowState[dict[str, object]](data={"spec_artifact": "already_exists"})
        step_def = create_step_def(outputs=("spec_artifact",))
        ref = ArtifactRef(
            artifact_id=ArtifactId("art-100"),
            version=1,
            content_hash=DUMMY_HASH,
        )

        with pytest.raises(StateValidationError) as exc_info:
            StateManager.commit_approved_artifact(pre_state, step_def, ref)

        assert "already exists in workflow state" in str(exc_info.value)

