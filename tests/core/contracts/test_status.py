"""Unit tests for lifecycle and outcome status enums."""

import json
from src.core.contracts.status import (
    ApprovalStatus,
    StepOutcome,
    TaskStatus,
    WorkflowStatus,
)


def test_workflow_status_values():
    expected = {
        "PENDING",
        "RUNNING",
        "WAITING_FOR_APPROVAL",
        "PAUSED",
        "SUSPENDED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
    actual = {s.value for s in WorkflowStatus}
    assert actual == expected

    # Verify semantic distinction
    assert WorkflowStatus.PAUSED != WorkflowStatus.SUSPENDED
    assert WorkflowStatus.PAUSED.value == "PAUSED"
    assert WorkflowStatus.SUSPENDED.value == "SUSPENDED"


def test_task_status_values():
    expected = {
        "PENDING",
        "READY",
        "RUNNING",
        "RETRYING",
        "SUCCEEDED",
        "FAILED",
        "SKIPPED",
        "CANCELLED",
    }
    actual = {s.value for s in TaskStatus}
    assert actual == expected


def test_approval_status_values():
    expected = {"PENDING", "APPROVED", "REJECTED", "CHANGES_REQUESTED"}
    actual = {s.value for s in ApprovalStatus}
    assert actual == expected


def test_step_outcome_values():
    expected = {"SUCCEEDED", "FAILED", "SKIPPED", "WAITING_FOR_APPROVAL"}
    actual = {s.value for s in StepOutcome}
    assert actual == expected


def test_enum_json_serialization():
    payload = {
        "workflow": WorkflowStatus.RUNNING,
        "task": TaskStatus.SUCCEEDED,
        "approval": ApprovalStatus.APPROVED,
        "outcome": StepOutcome.WAITING_FOR_APPROVAL,
    }
    dumped = json.dumps(payload)
    loaded = json.loads(dumped)

    assert loaded["workflow"] == "RUNNING"
    assert loaded["task"] == "SUCCEEDED"
    assert loaded["approval"] == "APPROVED"
    assert loaded["outcome"] == "WAITING_FOR_APPROVAL"

