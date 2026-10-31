"""Workflow metadata and generic execution context contracts."""

from datetime import datetime, timezone
from typing import Generic, TypeVar
from pydantic import BaseModel, Field

from src.core.contracts.ids import NonEmptyStr, RunId
from src.core.contracts.status import WorkflowStatus

TState = TypeVar("TState")


class WorkflowMetadata(BaseModel):
    """
    Metadata describing a specific workflow execution run.

    Timestamp Semantics:
    updated_at is initialized to created_at and is not automatically modified
    by Pydantic. The future engine/orchestration service must explicitly update
    updated_at whenever the workflow status transitions or state is committed.
    """

    run_id: RunId
    workflow_id: NonEmptyStr
    workflow_version: NonEmptyStr
    status: WorkflowStatus = WorkflowStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of last state commit or status transition",
    )


class WorkflowContext(BaseModel, Generic[TState]):
    """Generic container pairing workflow run metadata with a typed or untyped state snapshot."""

    metadata: WorkflowMetadata
    state: TState
