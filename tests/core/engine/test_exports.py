"""Unit tests for src.core.engine public package exports."""

import src.core.engine as engine_pkg


def test_exports_presence_and_uniqueness():
    assert hasattr(engine_pkg, "__all__")
    exports = engine_pkg.__all__
    assert isinstance(exports, list)
    assert len(exports) == len(set(exports)), "Duplicate export detected in __all__"


def test_all_exports_importable():
    for name in engine_pkg.__all__:
        assert hasattr(engine_pkg, name), f"Symbol '{name}' listed in __all__ but missing from package"


def test_expected_symbols_present():
    expected = {
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
    }
    assert set(engine_pkg.__all__) == expected
