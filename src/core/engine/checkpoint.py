"""CheckpointRecord schema, CheckpointStore protocol, and InMemoryCheckpointStore."""

from collections.abc import Mapping
import copy
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.contracts.approval import ApprovalRequest
from src.core.contracts.artifact import Sha256Hash
from src.core.contracts.ids import NonEmptyStr, RunId, StepId
from src.core.contracts.status import WorkflowStatus
from src.core.contracts.task import TaskRecord
from src.core.engine.errors import (
    CheckpointNotFoundError,
    CheckpointPersistError,
    CheckpointRollbackConflictError,
)
from src.core.workflow.state import WorkflowState


class CheckpointRecord(BaseModel):
    """
    Immutable state and execution snapshot capturing complete workflow progress.

    Captures sufficient context to deterministically inspect or restore execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    run_id: RunId
    workflow_id: NonEmptyStr
    definition_version: NonEmptyStr
    definition_hash: Sha256Hash
    sequence_number: int = Field(ge=0, description="Strictly monotonic checkpoint counter")
    workflow_status: WorkflowStatus
    state_snapshot: WorkflowState[dict[str, Any]]
    task_records: Mapping[StepId, TaskRecord] = Field(default_factory=dict)
    pending_approval: ApprovalRequest | None = None
    execution_generations: Mapping[StepId, int] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        validate_default=True,
    )
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", mode="after")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        """Reject naive datetimes and normalize timezone-aware timestamps to UTC."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at timestamp must be timezone-aware")
        return v.astimezone(timezone.utc)


@runtime_checkable
class CheckpointStore(Protocol):
    """Storage contract for workflow execution checkpoints."""

    async def save(self, record: CheckpointRecord) -> None:
        """Persist a checkpoint record."""
        ...

    async def get_latest(self, run_id: RunId | str) -> CheckpointRecord | None:
        """Get the latest checkpoint record for a run."""
        ...

    async def get_by_sequence(
        self,
        run_id: RunId | str,
        sequence_number: int,
    ) -> CheckpointRecord | None:
        """Get a specific checkpoint record by sequence number."""
        ...

    async def list_history(self, run_id: RunId | str) -> list[CheckpointRecord]:
        """List all checkpoint records for a run in ascending sequence order."""
        ...

    async def rollback_latest(
        self,
        run_id: RunId | str,
        expected_sequence_number: int,
    ) -> None:
        """
        Roll back the latest checkpoint if and only if sequence matches expected.

        Raises:
            CheckpointNotFoundError: If no checkpoints exist or expected sequence is not found.
            CheckpointRollbackConflictError: If a newer checkpoint exists.
        """
        ...


class InMemoryCheckpointStore:
    """
    In-memory reference implementation of CheckpointStore.

    Enforces strict monotonic sequence ordering, Copy-on-Write snapshot isolation,
    and safe sequence-checked rollbacks.
    """

    def __init__(self) -> None:
        self._runs: dict[str, list[CheckpointRecord]] = {}

    async def save(self, record: CheckpointRecord) -> None:
        """
        Persist a checkpoint record with sequence validation and snapshot isolation.

        Raises CheckpointPersistError if sequence_number is non-monotonic or introduces gaps.
        """
        if not isinstance(record, CheckpointRecord):
            raise TypeError(f"Expected CheckpointRecord, got {type(record).__name__}")

        run_id = str(record.run_id)
        if run_id not in self._runs:
            self._runs[run_id] = []

        history = self._runs[run_id]
        if history:
            last = history[-1]
            if record.workflow_id != last.workflow_id:
                raise CheckpointPersistError(
                    f"Workflow ID mismatch for run '{run_id}': expected '{last.workflow_id}', got '{record.workflow_id}'"
                )
            if record.definition_hash != last.definition_hash:
                raise CheckpointPersistError(
                    f"Definition hash mismatch for run '{run_id}': expected '{last.definition_hash}', got '{record.definition_hash}'"
                )
            if record.sequence_number <= last.sequence_number:
                raise CheckpointPersistError(
                    f"Stale sequence number {record.sequence_number}; latest is {last.sequence_number}"
                )
            if record.sequence_number > last.sequence_number + 1:
                raise CheckpointPersistError(
                    f"Sequence gap detected: expected {last.sequence_number + 1}, got {record.sequence_number}"
                )
        else:
            if record.sequence_number != 0:
                raise CheckpointPersistError(
                    f"Initial checkpoint must have sequence_number 0, got {record.sequence_number}"
                )

        # Store an isolated deep copy
        self._runs[run_id].append(copy.deepcopy(record))

    async def get_latest(self, run_id: RunId | str) -> CheckpointRecord | None:
        """Get the latest checkpoint record for a run, returned as an isolated snapshot."""
        history = self._runs.get(str(run_id))
        return copy.deepcopy(history[-1]) if history else None

    async def get_by_sequence(
        self,
        run_id: RunId | str,
        sequence_number: int,
    ) -> CheckpointRecord | None:
        """Get a specific checkpoint record by sequence number."""
        history = self._runs.get(str(run_id), [])
        for rec in history:
            if rec.sequence_number == sequence_number:
                return copy.deepcopy(rec)
        return None

    async def list_history(self, run_id: RunId | str) -> list[CheckpointRecord]:
        """List all checkpoint records for a run in ascending sequence order."""
        history = self._runs.get(str(run_id), [])
        return [copy.deepcopy(rec) for rec in history]

    async def rollback_latest(
        self,
        run_id: RunId | str,
        expected_sequence_number: int,
    ) -> None:
        """
        Roll back the latest checkpoint matching expected_sequence_number.

        Raises:
            CheckpointNotFoundError: If no checkpoints exist or expected sequence is not latest.
            CheckpointRollbackConflictError: If a newer checkpoint has already been written.
        """
        run_id_str = str(run_id)
        history = self._runs.get(run_id_str)
        if not history:
            raise CheckpointNotFoundError(f"No checkpoints found for run '{run_id_str}' to rollback")

        latest = history[-1]
        if latest.sequence_number > expected_sequence_number:
            raise CheckpointRollbackConflictError(
                f"Cannot rollback sequence {expected_sequence_number}: latest checkpoint "
                f"has newer sequence {latest.sequence_number}"
            )

        if latest.sequence_number < expected_sequence_number:
            raise CheckpointNotFoundError(
                f"Cannot rollback sequence {expected_sequence_number}: latest checkpoint "
                f"has sequence {latest.sequence_number}"
            )

        # Exact sequence match -> safe rollback
        history.pop()

    def clear(self) -> None:
        """Clear all stored checkpoints."""
        self._runs.clear()

