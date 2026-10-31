"""Task execution tracking record and runtime context contracts."""

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field

from src.core.contracts.errors import ExecutionError
from src.core.contracts.ids import RunId, StepId, TaskId
from src.core.contracts.status import TaskStatus


class TaskRecord(BaseModel):
    """
    Lifecycle tracking record for an individual computational task.

    Timestamp Semantics:
    created_at is assigned at instantiation. started_at and completed_at default
    to None and are explicitly populated by the future task execution manager.
    """

    task_id: TaskId
    run_id: RunId
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: ExecutionError | None = None


class TaskContext(BaseModel):
    """Execution context and parameters provided to an executing task."""

    task_id: TaskId
    run_id: RunId
    step_id: StepId
    inputs: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

