"""Unit tests verifying public exports from src.core.workflow."""

import src.core.workflow as workflow


def test_public_exports_completeness_and_integrity():
    expected_exports = {
        # Definitions
        "WorkflowDefinition",
        "WorkflowInputDefinition",
        "StepDefinition",
        "Capability",
        # Plan & Stages
        "ExecutionPlan",
        "ExecutionStage",
        # State
        "WorkflowState",
        # Compiler & Hashing
        "WorkflowCompiler",
        "compute_canonical_workflow_hash",
        # Errors
        "WorkflowValidationError",
        "CircularDependencyError",
        "UnknownDependencyError",
        "SelfDependencyError",
        "DuplicateStepIdError",
        "DuplicateOutputProducerError",
        "OutputOverwritesInputError",
        "UnsatisfiedStateInputError",
        "OptionalInputAsRequiredDependencyError",
        "DuplicateKeyDeclarationError",
    }

    # Verify __all__ exists and is a sequence
    assert hasattr(workflow, "__all__"), "src.core.workflow must define __all__"
    assert isinstance(workflow.__all__, (list, tuple))

    # Verify no duplicate entries in __all__
    assert len(workflow.__all__) == len(set(workflow.__all__)), "Duplicate entries found in __all__"

    # Verify __all__ matches expected public symbols
    assert set(workflow.__all__) == expected_exports

    # Verify all symbols in __all__ are importable and accessible on the package
    for symbol in expected_exports:
        assert hasattr(workflow, symbol), f"Symbol '{symbol}' declared in __all__ is not attribute of src.core.workflow"


def test_internal_helpers_not_exported():
    internal_helpers = [
        "freeze_value",
        "unfreeze_value",
        "validate_json_serializable",
        "_check_cycles",
    ]

    for helper in internal_helpers:
        assert helper not in workflow.__all__, f"Internal helper '{helper}' should not be in __all__"

