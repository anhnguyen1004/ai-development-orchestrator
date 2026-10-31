"""Unit tests for WorkflowMetadata, WorkflowContext, TaskRecord, and TaskContext."""

from datetime import datetime, timezone
from pydantic import BaseModel

from src.core.contracts.status import TaskStatus, WorkflowStatus
from src.core.contracts.step import StepOutcome, StepResult
from src.core.contracts.task import TaskContext, TaskRecord
from src.core.contracts.workflow import WorkflowContext, WorkflowMetadata


class SampleTypedState(BaseModel):
    repository_url: str
    target_branch: str
    files_processed: list[str]


def test_workflow_and_step_generic_with_dict():
    metadata = WorkflowMetadata(
        run_id="run-001",
        workflow_id="repo-analysis",
        workflow_version="1.0.0",
    )
    raw_dict_state = {"repo": "ai-orchestrator", "branch": "main"}
    ctx = WorkflowContext[dict](metadata=metadata, state=raw_dict_state)

    assert ctx.metadata.run_id == "run-001"
    assert ctx.state["repo"] == "ai-orchestrator"

    step_res = StepResult[dict](
        outcome=StepOutcome.SUCCEEDED,
        current_state=raw_dict_state,
    )
    assert step_res.current_state["branch"] == "main"


def test_workflow_and_step_generic_with_pydantic_model():
    metadata = WorkflowMetadata(
        run_id="run-002",
        workflow_id="code-generation",
        workflow_version="2.1.0",
        status=WorkflowStatus.RUNNING,
    )
    typed_state = SampleTypedState(
        repository_url="https://github.com/org/repo",
        target_branch="feat/auth",
        files_processed=["auth.py", "test_auth.py"],
    )
    ctx = WorkflowContext[SampleTypedState](metadata=metadata, state=typed_state)

    assert ctx.state.target_branch == "feat/auth"
    assert "auth.py" in ctx.state.files_processed

    step_res = StepResult[SampleTypedState](
        outcome=StepOutcome.SUCCEEDED,
        current_state=typed_state,
    )
    assert step_res.current_state.repository_url == "https://github.com/org/repo"


def test_workflow_metadata_timestamps():
    before = datetime.now(timezone.utc)
    metadata = WorkflowMetadata(
        run_id="run-003",
        workflow_id="test-wf",
        workflow_version="1.0.0",
    )
    after = datetime.now(timezone.utc)

    assert before <= metadata.created_at <= after
    assert before <= metadata.updated_at <= after
    assert metadata.status == WorkflowStatus.PENDING

    # Documented behavior: updated_at does not change automatically in Pydantic;
    # it must be explicitly set by the engine when status transitions.
    original_updated_at = metadata.updated_at
    metadata.status = WorkflowStatus.RUNNING
    assert metadata.updated_at == original_updated_at


def test_task_record_optional_timestamps():
    # In PENDING status, started_at and completed_at must be None by default
    task = TaskRecord(
        task_id="task-001",
        run_id="run-001",
    )
    assert task.status == TaskStatus.PENDING
    assert task.created_at is not None
    assert task.started_at is None
    assert task.completed_at is None
    assert task.error is None

    # When transitioning to RUNNING and SUCCEEDED, timestamps are populated explicitly
    now = datetime.now(timezone.utc)
    task.status = TaskStatus.RUNNING
    task.started_at = now
    assert task.started_at == now

    task.status = TaskStatus.SUCCEEDED
    task.completed_at = now
    assert task.completed_at == now


def test_task_context():
    ctx = TaskContext(
        task_id="task-002",
        run_id="run-002",
        step_id="step_code_gen",
        inputs={"prompt": "Create JWT handler"},
        metadata={"worker_id": "worker-1"},
    )
    assert ctx.task_id == "task-002"
    assert ctx.inputs["prompt"] == "Create JWT handler"
    assert ctx.metadata["worker_id"] == "worker-1"

