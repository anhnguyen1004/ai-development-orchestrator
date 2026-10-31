"""Lifecycle and outcome status enums for workflows, tasks, steps, and approvals."""

from enum import Enum


class WorkflowStatus(str, Enum):
    """Lifecycle status of a complete workflow run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    PAUSED = "PAUSED"          # Explicitly paused by user; can be resumed
    SUSPENDED = "SUSPENDED"    # Blocked by an unresolved system condition requiring intervention
    COMPLETED = "COMPLETED"    # Successfully finished all steps
    FAILED = "FAILED"          # Terminated due to unrecoverable error
    CANCELLED = "CANCELLED"    # Terminated by user cancellation or reviewer rejection


class TaskStatus(str, Enum):
    """Lifecycle status of an individual computational task."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(str, Enum):
    """Lifecycle status of a human approval request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class StepOutcome(str, Enum):
    """Immediate outcome returned by a step execution."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"

