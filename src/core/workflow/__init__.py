"""Workflow definition, validation, and compilation package."""

from src.core.workflow.compiler import WorkflowCompiler
from src.core.workflow.definition import (
    WorkflowDefinition,
    WorkflowInputDefinition,
)
from src.core.workflow.errors import (
    CircularDependencyError,
    DuplicateKeyDeclarationError,
    DuplicateOutputProducerError,
    DuplicateStepIdError,
    OptionalInputAsRequiredDependencyError,
    OutputOverwritesInputError,
    SelfDependencyError,
    UnknownDependencyError,
    UnsatisfiedStateInputError,
    WorkflowValidationError,
)
from src.core.workflow.hashing import compute_canonical_workflow_hash
from src.core.workflow.plan import (
    ExecutionPlan,
    ExecutionStage,
)
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import (
    Capability,
    StepDefinition,
)

__all__ = [
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
]

