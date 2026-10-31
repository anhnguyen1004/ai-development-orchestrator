"""Unit tests verifying public exports from src.core.contracts."""

import src.core.contracts as contracts


def test_public_exports():
    expected_exports = {
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
    }

    assert set(contracts.__all__) == expected_exports

    for symbol in expected_exports:
        assert hasattr(contracts, symbol), f"Symbol {symbol} missing in src.core.contracts"

