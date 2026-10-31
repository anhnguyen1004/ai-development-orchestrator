"""Domain exceptions for the AI Development Orchestrator execution engine."""


class EngineError(Exception):
    """Base exception for all execution engine domain errors."""


class StateValidationError(EngineError):
    """Raised when a StepResult returned state fails snapshot or output validation."""


class StateContractViolationError(EngineError):
    """Raised when a step is missing a required input that was not caused by a skipped ancestor."""


class ApprovalContractError(EngineError):
    """Raised when an approval-required step violates the single-output or approval contract."""


class ArtifactStorageVerificationError(EngineError):
    """Raised when an artifact cannot be verified in storage (missing, corrupted, or hash mismatch)."""


class CheckpointPersistError(EngineError):
    """Raised when a checkpoint cannot be saved to storage."""


class CheckpointRollbackConflictError(EngineError):
    """Raised when a checkpoint rollback fails because expected sequence is not latest."""


class CheckpointNotFoundError(EngineError):
    """Raised when a checkpoint expected for rollback does not exist in storage."""


class EventSinkError(EngineError):
    """Raised when an event sink fails to record an event."""


class CoordinatorConsistencyError(EngineError):
    """Raised when a critical split-brain inconsistency occurs (e.g. event failure and rollback failure)."""


class PlanHashMismatchError(EngineError):
    """Raised when the execution plan definition hash does not match the checkpoint definition hash."""


class ApprovalExpiredError(EngineError):
    """Raised when an approval decision references a drifted or expired artifact version."""


class IllegalStateTransitionError(EngineError):
    """Raised when attempting an illegal transition on a workflow or task lifecycle."""


class WorkflowTerminatedError(EngineError):
    """Raised when attempting an operation on a terminated workflow run."""


class UnregisteredCapabilityError(EngineError, LookupError):
    """Raised when resolving a capability that has no registered StepExecutor."""


class DuplicateCapabilityError(EngineError, ValueError):
    """Raised when attempting to register a capability that is already registered."""
