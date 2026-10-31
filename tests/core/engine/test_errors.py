"""Unit tests for engine error taxonomy and exception hierarchy."""

import pytest

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

ENGINE_ERRORS = [
    StateValidationError,
    StateContractViolationError,
    ApprovalContractError,
    ArtifactStorageVerificationError,
    CheckpointPersistError,
    CheckpointRollbackConflictError,
    CheckpointNotFoundError,
    EventSinkError,
    CoordinatorConsistencyError,
    PlanHashMismatchError,
    ApprovalExpiredError,
    IllegalStateTransitionError,
    WorkflowTerminatedError,
    UnregisteredCapabilityError,
    DuplicateCapabilityError,
]


class TestEngineErrors:
    @pytest.mark.parametrize("err_cls", ENGINE_ERRORS)
    def test_inherits_from_engine_error(self, err_cls):
        assert issubclass(err_cls, EngineError)
        assert issubclass(err_cls, Exception)

    @pytest.mark.parametrize("err_cls", ENGINE_ERRORS)
    def test_instantiation_and_catch(self, err_cls):
        msg = f"Test failure for {err_cls.__name__}"
        with pytest.raises(EngineError) as exc_info:
            raise err_cls(msg)

        assert issubclass(exc_info.type, err_cls)
        assert msg in str(exc_info.value)
