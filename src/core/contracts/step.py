"""Step execution results and outcome consistency rules."""

from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.contracts.approval import ApprovalRequest
from src.core.contracts.errors import ExecutionError
from src.core.contracts.status import StepOutcome

TState = TypeVar("TState")


class StepResult(BaseModel, Generic[TState]):
    """
    Complete state snapshot returned by a step execution.

    Immutability Note:
    frozen=True protects top-level model fields from reassignment. When TState is
    a dictionary, Python does not deeply freeze nested keys. Callers and the engine
    must adhere to the Copy-on-Write convention and treat current_state as read-only.
    """

    model_config = ConfigDict(frozen=True)

    outcome: StepOutcome
    current_state: TState = Field(description="Complete state snapshot at step completion")
    approval_request: ApprovalRequest | None = None
    error: ExecutionError | None = None
    skip_reason: str | None = None

    @model_validator(mode="after")
    def validate_outcome_consistency(self) -> "StepResult[TState]":
        """Enforce strict field consistency for each StepOutcome."""
        if self.outcome == StepOutcome.SUCCEEDED:
            if self.error is not None:
                raise ValueError("SUCCEEDED step cannot contain an error")
            if self.approval_request is not None:
                raise ValueError("SUCCEEDED step cannot contain an approval_request")
            if self.skip_reason is not None:
                raise ValueError("SUCCEEDED step cannot contain a skip_reason")

        elif self.outcome == StepOutcome.FAILED:
            if self.error is None:
                raise ValueError("FAILED step requires an error")
            if self.approval_request is not None:
                raise ValueError("FAILED step cannot contain an approval_request")
            if self.skip_reason is not None:
                raise ValueError("FAILED step cannot contain a skip_reason")

        elif self.outcome == StepOutcome.SKIPPED:
            if not self.skip_reason or not self.skip_reason.strip():
                raise ValueError("SKIPPED step requires a non-empty skip_reason")
            if self.error is not None:
                raise ValueError("SKIPPED step cannot contain an error")
            if self.approval_request is not None:
                raise ValueError("SKIPPED step cannot contain an approval_request")

        elif self.outcome == StepOutcome.WAITING_FOR_APPROVAL:
            if self.approval_request is None:
                raise ValueError("WAITING_FOR_APPROVAL step requires an approval_request")
            if self.error is not None:
                raise ValueError("WAITING_FOR_APPROVAL step cannot contain an error")
            if self.skip_reason is not None:
                raise ValueError("WAITING_FOR_APPROVAL step cannot contain a skip_reason")

        return self
