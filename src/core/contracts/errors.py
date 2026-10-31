"""Error taxonomy and structured execution error records."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from src.core.contracts.ids import NonEmptyStr


class ErrorCategory(str, Enum):
    """
    Taxonomy describing failure origin and nature.
    
    Note: Error categories describe failure type, not retry policy.
    Retry behavior is decoupled and managed by the future Execution Engine.
    """

    TRANSIENT = "TRANSIENT"            # Network blip, 429 rate limit, 503 service unavailable
    PERMANENT = "PERMANENT"            # Schema mismatch, syntax error, 401 unauthorized
    SYSTEM = "SYSTEM"                  # Host/orchestrator crash, out of memory, disk error
    USER_CANCELLED = "USER_CANCELLED"  # Explicitly aborted by user


class ExecutionError(BaseModel):
    """Immutable record of an error occurring during task or step execution."""

    model_config = ConfigDict(frozen=True)

    category: ErrorCategory
    message: NonEmptyStr
    code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    stack_trace: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

