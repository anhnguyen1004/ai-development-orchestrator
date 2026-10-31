"""Public contracts layer for the AI Development Orchestration platform."""

from src.core.contracts.approval import (
    ApprovalDecision,
    ApprovalRequest,
    DecisionAction,
)
from src.core.contracts.artifact import (
    ArtifactRef,
    Sha256Hash,
)
from src.core.contracts.errors import (
    ErrorCategory,
    ExecutionError,
)
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
from src.core.contracts.status import (
    ApprovalStatus,
    StepOutcome,
    TaskStatus,
    WorkflowStatus,
)
from src.core.contracts.step import StepResult
from src.core.contracts.task import TaskContext, TaskRecord
from src.core.contracts.workflow import WorkflowContext, WorkflowMetadata

__all__ = [
    # IDs
    "NonEmptyStr",
    "RunId",
    "RunIdType",
    "TaskId",
    "TaskIdType",
    "StepId",
    "StepIdType",
    "ApprovalId",
    "ApprovalIdType",
    "OperationId",
    "OperationIdType",
    "ArtifactId",
    "ArtifactIdType",
    # Statuses & Outcomes
    "WorkflowStatus",
    "TaskStatus",
    "ApprovalStatus",
    "StepOutcome",
    # Errors
    "ErrorCategory",
    "ExecutionError",
    # Artifacts
    "ArtifactRef",
    "Sha256Hash",
    # Approvals
    "DecisionAction",
    "ApprovalRequest",
    "ApprovalDecision",
    # Step Execution
    "StepResult",
    # Workflow
    "WorkflowMetadata",
    "WorkflowContext",
    # Task
    "TaskRecord",
    "TaskContext",
]

