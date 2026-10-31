"""Unit tests for WorkflowState snapshot container and immutability semantics."""

import pytest
from pydantic import BaseModel, ValidationError

from src.core.workflow.state import WorkflowState


class CustomDevState(BaseModel):
    repo_url: str
    branch: str
    is_tested: bool = False


def test_workflow_state_with_dict():
    raw_dict = {"repo_url": "https://github.com/org/repo", "counter": 1}
    state = WorkflowState[dict](
        schema_id="dict[str, Any]",
        data=raw_dict,
    )
    assert state.schema_id == "dict[str, Any]"
    assert state.data["counter"] == 1


def test_workflow_state_with_pydantic_model():
    model_data = CustomDevState(
        repo_url="https://github.com/org/repo",
        branch="main",
        is_tested=True,
    )
    state = WorkflowState[CustomDevState](
        schema_id="CustomDevState.v1",
        data=model_data,
    )
    assert state.schema_id == "CustomDevState.v1"
    assert state.data.is_tested is True


def test_workflow_state_top_level_frozen():
    state = WorkflowState[dict](
        data={"k": "v"},
    )
    with pytest.raises(ValidationError):
        state.schema_id = "new_schema"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        state.data = {"new": "data"}  # type: ignore[misc]


def test_workflow_state_nested_dict_copy_on_write_limitation():
    """
    Demonstrates the documented architectural limitation:
    WorkflowState outer envelope is frozen, but Python dictionaries inside data
    remain mutable references in memory. Execution engine must follow Copy-on-Write.
    """
    initial_dict = {"items": [1, 2]}
    state = WorkflowState[dict](data=initial_dict)

    # Inner dictionary mutation is not prevented by frozen=True
    state.data["items"].append(3)
    assert state.data["items"] == [1, 2, 3]

