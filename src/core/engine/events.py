"""Execution audit events, EventType taxonomy, secret sanitization, and EventSink protocol."""

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from src.core.contracts.artifact import Sha256Hash
from src.core.contracts.ids import NonEmptyStr, RunId
from src.core.workflow.freezer import freeze_value, unfreeze_value, validate_json_serializable

SENSITIVE_KEY_PATTERNS = (
    "token",
    "password",
    "secret",
    "api_key",
    "auth",
    "credential",
    "private_key",
)


def is_sensitive_key(key: str) -> bool:
    """Check if a dictionary key matches any sensitive pattern."""
    k_lower = key.lower()
    return any(p in k_lower for p in SENSITIVE_KEY_PATTERNS)


def sanitize_payload(value: Any) -> Any:
    """
    Recursively sanitize sensitive keys in a dictionary or list payload.

    Replaces sensitive values with '[REDACTED]' and returns an independent copy.
    """
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            k_str = str(k)
            if is_sensitive_key(k_str):
                cleaned[k_str] = "[REDACTED]"
            else:
                cleaned[k_str] = sanitize_payload(v)
        return cleaned
    elif isinstance(value, (list, tuple)):
        return [sanitize_payload(elem) for elem in value]
    return value


class EventType(str, Enum):
    """Taxonomy of discrete execution lifecycle audit events."""

    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    TASK_READY = "TASK_READY"
    TASK_STARTED = "TASK_STARTED"
    TASK_RETRY = "TASK_RETRY"
    STEP_EXECUTED = "STEP_EXECUTED"
    TASK_SUCCEEDED = "TASK_SUCCEEDED"
    TASK_FAILED = "TASK_FAILED"
    TASK_SKIPPED = "TASK_SKIPPED"
    TASK_CANCELLED = "TASK_CANCELLED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    WORKFLOW_SUSPENDED = "WORKFLOW_SUSPENDED"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    CHECKPOINT_COMMITTED = "CHECKPOINT_COMMITTED"
    TASK_CANCELLATION_TIMED_OUT = "TASK_CANCELLATION_TIMED_OUT"


class ExecutionEvent(BaseModel):
    """
    Immutable audit record representing a discrete runtime lifecycle transition.

    Enforces that definition_hash is pinned to the compiled execution plan and
    sequence_number is strictly non-negative.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    event_id: str
    run_id: RunId
    workflow_id: NonEmptyStr
    workflow_version: NonEmptyStr
    definition_hash: Sha256Hash
    sequence_number: int = Field(ge=0, description="Monotonically increasing sequence number")
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("payload", mode="after")
    @classmethod
    def validate_and_sanitize_payload(cls, v: Any) -> Mapping[str, Any]:
        validate_json_serializable(v)
        sanitized = sanitize_payload(v)
        return freeze_value(sanitized)

    @field_serializer("payload")
    def serialize_payload(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return unfreeze_value(v)


@runtime_checkable
class EventSink(Protocol):
    """Protocol for recording execution audit events."""

    async def record(self, event: ExecutionEvent) -> None:
        """
        Record a single execution event atomically.

        Contract:
        - Must be all-or-nothing: if record() raises an exception, no event is committed.
        - Must store a sanitized snapshot isolated from caller-owned payload mutations.
        """
        ...


class InMemoryEventSink:
    """
    In-memory implementation of EventSink.

    Provides atomic event recording, payload sanitization isolation, and deterministic ordering.
    """

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []

    async def record(self, event: ExecutionEvent) -> None:
        """
        Record an execution event atomically.

        Validates that event is an ExecutionEvent, creates a sanitized snapshot,
        and appends it to storage. If validation fails, storage is untouched.
        """
        if not isinstance(event, ExecutionEvent):
            raise TypeError(f"Expected ExecutionEvent, got {type(event).__name__}")

        # Re-verify and sanitize payload locally before mutating state
        raw_payload = unfreeze_value(event.payload)
        validate_json_serializable(raw_payload)
        sanitized = sanitize_payload(raw_payload)

        # Build clean immutable copy of event with sanitized payload
        stored_event = event.model_copy(
            update={"payload": freeze_value(sanitized)},
            deep=True,
        )

        # Atomic commit to list in local memory
        self._events.append(stored_event)

    def get_events(self, run_id: RunId | str | None = None) -> list[ExecutionEvent]:
        """Return recorded events, optionally filtered by run_id."""
        if run_id is None:
            return list(self._events)
        target = str(run_id)
        return [e for e in self._events if str(e.run_id) == target]

    def get_events_by_type(
        self,
        event_type: EventType,
        run_id: RunId | str | None = None,
    ) -> list[ExecutionEvent]:
        """Return recorded events filtered by event_type and optional run_id."""
        events = self.get_events(run_id)
        return [e for e in events if e.event_type == event_type]

    def clear(self) -> None:
        """Clear all stored events."""
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)

