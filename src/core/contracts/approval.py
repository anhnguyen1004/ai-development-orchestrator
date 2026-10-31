"""Human-in-the-loop approval request and decision contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from src.core.contracts.artifact import ArtifactRef, Sha256Hash
from src.core.contracts.ids import ApprovalId, ArtifactId, NonEmptyStr, RunId, StepId
from src.core.contracts.status import ApprovalStatus


class DecisionAction(str, Enum):
    """Reviewer decision actions."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class ApprovalRequest(BaseModel):
    """
    Ticket requesting human review of a specific artifact version.
    
    Cross-Record Validation Note:
    This model defines the request structure. Validating cross-record consistency
    between requests and decisions is the responsibility of the future Approval Gate/Engine.
    """

    approval_id: ApprovalId
    run_id: RunId
    step_id: StepId
    artifact: ArtifactRef
    summary: NonEmptyStr
    status: ApprovalStatus = ApprovalStatus.PENDING
    context_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalDecision(BaseModel):
    """
    Immutable record of a reviewer's decision on a specific artifact.
    
    Cross-Record Validation Note:
    approval_id must refer to the corresponding ApprovalRequest. The application/engine
    must verify that run_id, artifact_id, artifact_version, and artifact_hash_reviewed
    match the request and the current state snapshot before applying continuation logic.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: ApprovalId
    run_id: RunId
    action: DecisionAction
    reviewer: NonEmptyStr
    artifact_id: ArtifactId
    artifact_version: int = Field(ge=1)
    artifact_hash_reviewed: Sha256Hash
    comment: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

