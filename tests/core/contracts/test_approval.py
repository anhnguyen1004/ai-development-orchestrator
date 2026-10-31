"""Unit tests for ApprovalRequest, ApprovalDecision, and DecisionAction contracts."""

import pytest
from pydantic import ValidationError

from src.core.contracts.approval import (
    ApprovalDecision,
    ApprovalRequest,
    DecisionAction,
)
from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.status import ApprovalStatus

VALID_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_decision_action_values():
    expected = {"APPROVE", "REJECT", "REQUEST_CHANGES"}
    actual = {a.value for a in DecisionAction}
    assert actual == expected


def test_approval_request_creation():
    artifact = ArtifactRef(
        artifact_id="art-proposal-1",
        version=1,
        content_hash=VALID_HASH,
    )
    req = ApprovalRequest(
        approval_id="appr-001",
        run_id="run-101",
        step_id="draft_step",
        artifact=artifact,
        summary="Please approve the proposal draft",
        context_data={"estimated_hours": 12},
    )
    assert req.approval_id == "appr-001"
    assert req.run_id == "run-101"
    assert req.step_id == "draft_step"
    assert req.artifact.content_hash == VALID_HASH
    assert req.status == ApprovalStatus.PENDING
    assert req.context_data == {"estimated_hours": 12}
    assert req.created_at is not None


def test_approval_decision_valid_creation():
    decision = ApprovalDecision(
        approval_id="appr-001",
        run_id="run-101",
        action=DecisionAction.APPROVE,
        reviewer="senior_engineer",
        artifact_id="art-proposal-1",
        artifact_version=1,
        artifact_hash_reviewed=f"  {VALID_HASH.upper()}  ",
        comment="Looks solid, proceed.",
    )
    assert decision.approval_id == "appr-001"
    assert decision.run_id == "run-101"
    assert decision.action == DecisionAction.APPROVE
    assert decision.reviewer == "senior_engineer"
    # Verification that Sha256Hash normalization was applied
    assert decision.artifact_hash_reviewed == VALID_HASH
    assert decision.comment == "Looks solid, proceed."
    assert decision.decided_at is not None


def test_approval_decision_invalid_hash_rejected():
    with pytest.raises(ValidationError):
        ApprovalDecision(
            approval_id="appr-001",
            run_id="run-101",
            action=DecisionAction.REJECT,
            reviewer="reviewer_1",
            artifact_id="art-1",
            artifact_version=1,
            artifact_hash_reviewed="invalid-non-sha256",
        )


def test_approval_decision_invalid_version_rejected():
    with pytest.raises(ValidationError):
        ApprovalDecision(
            approval_id="appr-001",
            run_id="run-101",
            action=DecisionAction.APPROVE,
            reviewer="reviewer_1",
            artifact_id="art-1",
            artifact_version=0,
            artifact_hash_reviewed=VALID_HASH,
        )


def test_approval_decision_immutability():
    decision = ApprovalDecision(
        approval_id="appr-001",
        run_id="run-101",
        action=DecisionAction.REQUEST_CHANGES,
        reviewer="reviewer_1",
        artifact_id="art-1",
        artifact_version=1,
        artifact_hash_reviewed=VALID_HASH,
        comment="Fix formatting",
    )
    with pytest.raises(ValidationError):
        decision.action = DecisionAction.APPROVE  # type: ignore[misc]

