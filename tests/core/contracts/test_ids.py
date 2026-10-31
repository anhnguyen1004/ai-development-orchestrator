"""Unit tests for typed IDs and runtime string constraints."""

import pytest
from pydantic import BaseModel, ValidationError

from src.core.contracts.ids import (
    ApprovalId,
    ApprovalIdType,
    ArtifactId,
    ArtifactIdType,
    NonEmptyStr,
    OperationId,
    OperationIdType,
    RunId,
    RunIdType,
    StepId,
    StepIdType,
    TaskId,
    TaskIdType,
)


class DummyModel(BaseModel):
    non_empty: NonEmptyStr
    run_id: RunId
    task_id: TaskId
    step_id: StepId
    approval_id: ApprovalId
    operation_id: OperationId
    artifact_id: ArtifactId


def test_valid_ids_and_whitespace_normalization():
    model = DummyModel(
        non_empty="  hello  ",
        run_id="  run-101  ",
        task_id="task-202  ",
        step_id="  step-303",
        approval_id="  appr-404  ",
        operation_id=" op-505 ",
        artifact_id=" art-606 ",
    )
    assert model.non_empty == "hello"
    assert model.run_id == "run-101"
    assert model.task_id == "task-202"
    assert model.step_id == "step-303"
    assert model.approval_id == "appr-404"
    assert model.operation_id == "op-505"
    assert model.artifact_id == "art-606"


@pytest.mark.parametrize(
    "field_name",
    ["non_empty", "run_id", "task_id", "step_id", "approval_id", "operation_id", "artifact_id"],
)
def test_empty_string_rejection(field_name: str):
    valid_data = {
        "non_empty": "valid",
        "run_id": "r1",
        "task_id": "t1",
        "step_id": "s1",
        "approval_id": "a1",
        "operation_id": "o1",
        "artifact_id": "art1",
    }
    invalid_data = {**valid_data, field_name: ""}
    with pytest.raises(ValidationError) as exc_info:
        DummyModel.model_validate(invalid_data)
    assert field_name in str(exc_info.value)


@pytest.mark.parametrize(
    "field_name",
    ["non_empty", "run_id", "task_id", "step_id", "approval_id", "operation_id", "artifact_id"],
)
def test_whitespace_only_rejection(field_name: str):
    valid_data = {
        "non_empty": "valid",
        "run_id": "r1",
        "task_id": "t1",
        "step_id": "s1",
        "approval_id": "a1",
        "operation_id": "o1",
        "artifact_id": "art1",
    }
    invalid_data = {**valid_data, field_name: "     "}
    with pytest.raises(ValidationError) as exc_info:
        DummyModel.model_validate(invalid_data)
    assert field_name in str(exc_info.value)


def test_static_newtype_types():
    assert RunIdType is not TaskIdType
    assert StepIdType is not ApprovalIdType
    assert OperationIdType is not ArtifactIdType

