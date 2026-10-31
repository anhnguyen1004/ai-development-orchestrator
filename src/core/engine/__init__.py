"""Execution engine package: deterministic orchestration runtime."""

from src.core.engine.approval_gate import ApprovalGate
from src.core.engine.checkpoint import (
    CheckpointRecord,
    CheckpointStore,
    InMemoryCheckpointStore,
)
from src.core.engine.coordinator import (
    VALID_TASK_TRANSITIONS,
    VALID_WORKFLOW_TRANSITIONS,
    TransitionCoordinator,
)
from src.core.engine.errors import (
    ApprovalContractError,
    ApprovalExpiredError,
    ArtifactStorageVerificationError,
    CheckpointNotFoundError,
    CheckpointPersistError,
    CheckpointRollbackConflictError,
    CoordinatorConsistencyError,
    DuplicateCapabilityError,
    EngineError,
    EventSinkError,
    IllegalStateTransitionError,
    PlanHashMismatchError,
    StateContractViolationError,
    StateValidationError,
    UnregisteredCapabilityError,
    WorkflowTerminatedError,
)
from src.core.engine.events import (
    EventSink,
    EventType,
    ExecutionEvent,
    InMemoryEventSink,
    sanitize_payload,
)
from src.core.engine.retry import RetryPolicy
from src.core.engine.state_manager import StateManager
from src.core.engine.step_executor import (
    InMemoryStepRegistry,
    StepExecutor,
    StepRegistry,
)
from src.core.engine.types import (
    ArtifactStore,
    InMemoryArtifactStore,
    ReadOnlyStateView,
    StepExecutionContext,
    compute_executor_idempotency_key,
)

__all__ = [
    # Errors
    "EngineError",
    "StateValidationError",
    "StateContractViolationError",
    "ApprovalContractError",
    "ArtifactStorageVerificationError",
    "CheckpointPersistError",
    "CheckpointRollbackConflictError",
    "CheckpointNotFoundError",
    "EventSinkError",
    "CoordinatorConsistencyError",
    "PlanHashMismatchError",
    "ApprovalExpiredError",
    "IllegalStateTransitionError",
    "WorkflowTerminatedError",
    "UnregisteredCapabilityError",
    "DuplicateCapabilityError",
    # Types & Protocols
    "ReadOnlyStateView",
    "StepExecutionContext",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "compute_executor_idempotency_key",
    # Step Execution & Registry
    "StepExecutor",
    "StepRegistry",
    "InMemoryStepRegistry",
    # State Manager
    "StateManager",
    # Retry Policy
    "RetryPolicy",
    # Events & Sinks
    "ExecutionEvent",
    "EventType",
    "EventSink",
    "InMemoryEventSink",
    "sanitize_payload",
    # Approval Gate
    "ApprovalGate",
    # Checkpoint & Store
    "CheckpointRecord",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    # Coordinator & Transitions
    "TransitionCoordinator",
    "VALID_WORKFLOW_TRANSITIONS",
    "VALID_TASK_TRANSITIONS",
]
