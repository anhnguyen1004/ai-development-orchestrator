"""Runtime state snapshot container for workflow runs."""

from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

from src.core.contracts.ids import NonEmptyStr

TState = TypeVar("TState", bound=BaseModel | dict[str, Any])


class WorkflowState(BaseModel, Generic[TState]):
    """
    Pure data container for the shared state snapshot of a workflow execution.

    Immutability Specification:
    WorkflowState is a frozen outer envelope (model_config = ConfigDict(frozen=True)).
    Its underlying data object (a dict or Pydantic model) is NOT deeply frozen at runtime.
    The platform explicitly relies on the Copy-on-Write convention: execution engines must
    treat data as a read-only snapshot.
    """

    model_config = ConfigDict(frozen=True)

    schema_id: NonEmptyStr = Field(default="dict[str, Any]")
    data: TState = Field(description="The validated state snapshot")

