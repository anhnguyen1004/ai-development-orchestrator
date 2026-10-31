"""ApprovalGate for human-in-the-loop decision validation and artifact verification."""

from typing import Any
from src.core.contracts.approval import ApprovalDecision, ApprovalRequest, DecisionAction
from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.status import ApprovalStatus, WorkflowStatus
from src.core.engine.errors import (
    ApprovalContractError,
    ApprovalExpiredError,
    IllegalStateTransitionError,
    WorkflowTerminatedError,
)
from src.core.engine.state_manager import StateManager
from src.core.engine.types import ArtifactStore
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import StepDefinition


class ApprovalGate:
    """
    Validates human approval decisions against approval requests and verified stored artifacts.

    Guarantees that:
    1. Decisions are cryptographically bound to exact artifact versions and content hashes.
    2. Decisions referencing drifted, expired, or non-existent artifacts are strictly rejected.
    3. Candidate outputs are only committed upon valid APPROVE decisions via StateManager.
    4. Pre-approval state is completely preserved on REJECT and REQUEST_CHANGES.
    """

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._artifact_store = artifact_store

    async def validate_decision(
        self,
        decision: ApprovalDecision,
        request: ApprovalRequest,
        current_artifact_ref: ArtifactRef,
        workflow_status: WorkflowStatus,
    ) -> None:
        """
        Validate an ApprovalDecision against the request, current state reference, and artifact store.

        Raises:
            WorkflowTerminatedError: If workflow run is in a terminal status.
            IllegalStateTransitionError: If workflow status is not WAITING_FOR_APPROVAL.
            ApprovalContractError: If request is not PENDING, or IDs, version, or hash mismatch.
            ArtifactStorageVerificationError: If artifact is missing or corrupted in storage.
            ApprovalExpiredError: If current state artifact reference has drifted from request.
        """
        # 1. Workflow run status guard
        if workflow_status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        ):
            raise WorkflowTerminatedError(
                f"Cannot apply approval decision: workflow run is in terminal status '{workflow_status.value}'"
            )

        if workflow_status != WorkflowStatus.WAITING_FOR_APPROVAL:
            raise IllegalStateTransitionError(
                f"Cannot apply approval decision: workflow status is '{workflow_status.value}', "
                f"expected '{WorkflowStatus.WAITING_FOR_APPROVAL.value}'"
            )

        # 2. Approval request status guard
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalContractError(
                f"ApprovalRequest '{request.approval_id}' has already been resolved to '{request.status.value}'"
            )

        # 3. Identity and correlation checks
        if str(decision.approval_id) != str(request.approval_id):
            raise ApprovalContractError(
                f"approval_id mismatch: decision has '{decision.approval_id}', request has '{request.approval_id}'"
            )

        if str(decision.run_id) != str(request.run_id):
            raise ApprovalContractError(
                f"run_id mismatch: decision has '{decision.run_id}', request has '{request.run_id}'"
            )

        if str(decision.artifact_id) != str(request.artifact.artifact_id):
            raise ApprovalContractError(
                f"artifact_id mismatch: decision has '{decision.artifact_id}', "
                f"request has '{request.artifact.artifact_id}'"
            )

        # 4. Version check
        if decision.artifact_version != request.artifact.version:
            raise ApprovalContractError(
                f"artifact_version mismatch: decision has {decision.artifact_version}, "
                f"request has {request.artifact.version}"
            )

        # 5. Content hash match
        reviewed_hash = str(decision.artifact_hash_reviewed).strip().lower()
        request_hash = str(request.artifact.content_hash).strip().lower()
        if reviewed_hash != request_hash:
            raise ApprovalContractError(
                f"Reviewed content hash '{reviewed_hash}' does not match request hash '{request_hash}'"
            )

        # 6. Storage verification (verifies artifact exists and hash matches in store)
        await self._artifact_store.verify_artifact(request.artifact)

        # 7. Drift detection against current artifact reference
        if current_artifact_ref.version != request.artifact.version:
            raise ApprovalExpiredError(
                f"Artifact version drifted: request was for version {request.artifact.version}, "
                f"but current state contains version {current_artifact_ref.version}"
            )

        current_hash = str(current_artifact_ref.content_hash).strip().lower()
        if current_hash != reviewed_hash:
            raise ApprovalExpiredError(
                f"Artifact content hash drifted: decision reviewed '{reviewed_hash}', "
                f"but current state contains '{current_hash}'"
            )

    async def apply_decision(
        self,
        decision: ApprovalDecision,
        request: ApprovalRequest,
        current_artifact_ref: ArtifactRef,
        workflow_status: WorkflowStatus,
        step_def: StepDefinition,
        pre_state: WorkflowState[dict[str, Any]],
    ) -> tuple[ApprovalStatus, WorkflowState[dict[str, Any]]]:
        """
        Validate decision and apply continuation logic.

        - APPROVE: Validates and delegates artifact commit to StateManager.
        - REJECT: Marks request REJECTED, preserves pre_state.
        - REQUEST_CHANGES: Marks request CHANGES_REQUESTED, preserves pre_state.
        """
        await self.validate_decision(decision, request, current_artifact_ref, workflow_status)

        if decision.action == DecisionAction.APPROVE:
            committed_state = StateManager.commit_approved_artifact(
                pre_state=pre_state,
                step_def=step_def,
                artifact_ref=request.artifact,
            )
            return ApprovalStatus.APPROVED, committed_state

        if decision.action == DecisionAction.REJECT:
            return ApprovalStatus.REJECTED, pre_state

        if decision.action == DecisionAction.REQUEST_CHANGES:
            return ApprovalStatus.CHANGES_REQUESTED, pre_state

        raise ApprovalContractError(f"Unhandled DecisionAction: {decision.action}")

