"""Unit tests for ApprovalGate decision validation, artifact store verification, and outcome commits."""

from typing import Any, Coroutine
import pytest

from src.core.contracts.approval import ApprovalDecision, ApprovalRequest, DecisionAction
from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.ids import ApprovalId, ArtifactId, NonEmptyStr, RunId, StepId
from src.core.contracts.status import ApprovalStatus, WorkflowStatus
from src.core.engine.approval_gate import ApprovalGate
from src.core.engine.errors import (
    ApprovalContractError,
    ApprovalExpiredError,
    ArtifactStorageVerificationError,
    IllegalStateTransitionError,
    WorkflowTerminatedError,
)
from src.core.engine.types import InMemoryArtifactStore
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import Capability, StepDefinition

DUMMY_HASH = "c" * 64
DIFFERENT_HASH = "d" * 64


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion synchronously without requiring network/socketpair."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


def create_step_def(outputs: tuple[str, ...] = ("spec_doc",)) -> StepDefinition:
    return StepDefinition(
        step_id=StepId("step-spec"),
        name=NonEmptyStr("Generate Spec"),
        capability=Capability("specification.generate"),
        outputs=tuple(NonEmptyStr(o) for o in outputs),
    )


def create_request_and_decision(
    action: DecisionAction = DecisionAction.APPROVE,
    artifact_hash: str = DUMMY_HASH,
    version: int = 1,
    request_status: ApprovalStatus = ApprovalStatus.PENDING,
) -> tuple[ApprovalRequest, ApprovalDecision, ArtifactRef]:
    ref = ArtifactRef(
        artifact_id=ArtifactId("art-spec"),
        version=version,
        content_hash=artifact_hash,
        media_type="text/markdown",
    )
    req = ApprovalRequest(
        approval_id=ApprovalId("app-1"),
        run_id=RunId("run-100"),
        step_id=StepId("step-spec"),
        artifact=ref,
        summary=NonEmptyStr("Specification review"),
        status=request_status,
    )
    dec = ApprovalDecision(
        approval_id=ApprovalId("app-1"),
        run_id=RunId("run-100"),
        action=action,
        reviewer=NonEmptyStr("alice"),
        artifact_id=ArtifactId("art-spec"),
        artifact_version=version,
        artifact_hash_reviewed=artifact_hash,
    )
    return req, dec, ref


class TestApprovalGateDecisions:
    def test_approve_valid_decision_commits_artifact(self):
        store = InMemoryArtifactStore()
        store.put_artifact("art-spec", 1, DUMMY_HASH, payload={"content": "spec content"})
        gate = ApprovalGate(artifact_store=store)

        req, dec, ref = create_request_and_decision(action=DecisionAction.APPROVE)
        pre_state = WorkflowState[dict[str, object]](data={"base": 1})
        step_def = create_step_def(outputs=("spec_doc",))

        status, new_state = run_coro(
            gate.apply_decision(
                decision=dec,
                request=req,
                current_artifact_ref=ref,
                workflow_status=WorkflowStatus.WAITING_FOR_APPROVAL,
                step_def=step_def,
                pre_state=pre_state,
            )
        )

        assert status == ApprovalStatus.APPROVED
        assert new_state.data["base"] == 1
        assert new_state.data["spec_doc"] == {
            "artifact_id": "art-spec",
            "version": 1,
            "content_hash": DUMMY_HASH,
            "media_type": "text/markdown",
            "uri": None,
        }

    def test_reject_decision_does_not_commit_output(self):
        store = InMemoryArtifactStore()
        store.put_artifact("art-spec", 1, DUMMY_HASH)
        gate = ApprovalGate(artifact_store=store)

        req, dec, ref = create_request_and_decision(action=DecisionAction.REJECT)
        pre_state = WorkflowState[dict[str, object]](data={"base": 1})
        step_def = create_step_def(outputs=("spec_doc",))

        status, new_state = run_coro(
            gate.apply_decision(
                decision=dec,
                request=req,
                current_artifact_ref=ref,
                workflow_status=WorkflowStatus.WAITING_FOR_APPROVAL,
                step_def=step_def,
                pre_state=pre_state,
            )
        )

        assert status == ApprovalStatus.REJECTED
        assert new_state == pre_state
        assert "spec_doc" not in new_state.data

    def test_request_changes_decision_does_not_commit_output(self):
        store = InMemoryArtifactStore()
        store.put_artifact("art-spec", 1, DUMMY_HASH)
        gate = ApprovalGate(artifact_store=store)

        req, dec, ref = create_request_and_decision(action=DecisionAction.REQUEST_CHANGES)
        pre_state = WorkflowState[dict[str, object]](data={"base": 1})
        step_def = create_step_def(outputs=("spec_doc",))

        status, new_state = run_coro(
            gate.apply_decision(
                decision=dec,
                request=req,
                current_artifact_ref=ref,
                workflow_status=WorkflowStatus.WAITING_FOR_APPROVAL,
                step_def=step_def,
                pre_state=pre_state,
            )
        )

        assert status == ApprovalStatus.CHANGES_REQUESTED
        assert new_state == pre_state
        assert "spec_doc" not in new_state.data


class TestApprovalGateValidationErrors:
    def test_rejects_terminal_workflow_status(self):
        store = InMemoryArtifactStore()
        gate = ApprovalGate(artifact_store=store)
        req, dec, ref = create_request_and_decision()

        for term_status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
            with pytest.raises(WorkflowTerminatedError) as exc_info:
                run_coro(gate.validate_decision(dec, req, ref, term_status))
            assert "terminal status" in str(exc_info.value)

    def test_rejects_non_waiting_workflow_status(self):
        store = InMemoryArtifactStore()
        gate = ApprovalGate(artifact_store=store)
        req, dec, ref = create_request_and_decision()

        with pytest.raises(IllegalStateTransitionError) as exc_info:
            run_coro(gate.validate_decision(dec, req, ref, WorkflowStatus.RUNNING))
        assert "expected 'WAITING_FOR_APPROVAL'" in str(exc_info.value)

    def test_rejects_non_pending_request(self):
        store = InMemoryArtifactStore()
        gate = ApprovalGate(artifact_store=store)
        req, dec, ref = create_request_and_decision(request_status=ApprovalStatus.APPROVED)

        with pytest.raises(ApprovalContractError) as exc_info:
            run_coro(gate.validate_decision(dec, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "already been resolved to 'APPROVED'" in str(exc_info.value)

    def test_rejects_identity_mismatches(self):
        store = InMemoryArtifactStore()
        store.put_artifact("art-spec", 1, DUMMY_HASH)
        gate = ApprovalGate(artifact_store=store)
        req, dec, ref = create_request_and_decision()

        # approval_id mismatch
        bad_app_id = dec.model_copy(update={"approval_id": ApprovalId("app-other")})
        with pytest.raises(ApprovalContractError) as exc:
            run_coro(gate.validate_decision(bad_app_id, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "approval_id mismatch" in str(exc.value)

        # run_id mismatch
        bad_run = dec.model_copy(update={"run_id": RunId("run-other")})
        with pytest.raises(ApprovalContractError) as exc:
            run_coro(gate.validate_decision(bad_run, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "run_id mismatch" in str(exc.value)

        # artifact_id mismatch
        bad_art = dec.model_copy(update={"artifact_id": ArtifactId("art-other")})
        with pytest.raises(ApprovalContractError) as exc:
            run_coro(gate.validate_decision(bad_art, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "artifact_id mismatch" in str(exc.value)

        # version mismatch
        bad_ver = dec.model_copy(update={"artifact_version": 2})
        with pytest.raises(ApprovalContractError) as exc:
            run_coro(gate.validate_decision(bad_ver, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "artifact_version mismatch" in str(exc.value)

        # content hash mismatch
        bad_hash = dec.model_copy(update={"artifact_hash_reviewed": DIFFERENT_HASH})
        with pytest.raises(ApprovalContractError) as exc:
            run_coro(gate.validate_decision(bad_hash, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "Reviewed content hash" in str(exc.value)

    def test_rejects_artifact_missing_or_corrupted_in_store(self):
        store = InMemoryArtifactStore()
        gate = ApprovalGate(artifact_store=store)
        req, dec, ref = create_request_and_decision()

        # Missing in store
        with pytest.raises(ArtifactStorageVerificationError) as exc_missing:
            run_coro(gate.validate_decision(dec, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "not found in storage" in str(exc_missing.value)

        # Hash mismatch in store
        store.put_artifact("art-spec", 1, DIFFERENT_HASH)
        with pytest.raises(ArtifactStorageVerificationError) as exc_corrupt:
            run_coro(gate.validate_decision(dec, req, ref, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "content hash mismatch" in str(exc_corrupt.value)

    def test_rejects_drifted_current_artifact_reference(self):
        store = InMemoryArtifactStore()
        store.put_artifact("art-spec", 1, DUMMY_HASH)
        gate = ApprovalGate(artifact_store=store)
        req, dec, ref = create_request_and_decision()

        # Drifted version
        drifted_ver = ref.model_copy(update={"version": 2})
        with pytest.raises(ApprovalExpiredError) as exc_ver:
            run_coro(gate.validate_decision(dec, req, drifted_ver, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "Artifact version drifted" in str(exc_ver.value)

        # Drifted content hash
        drifted_hash = ref.model_copy(update={"content_hash": DIFFERENT_HASH})
        with pytest.raises(ApprovalExpiredError) as exc_hash:
            run_coro(gate.validate_decision(dec, req, drifted_hash, WorkflowStatus.WAITING_FOR_APPROVAL))
        assert "Artifact content hash drifted" in str(exc_hash.value)
