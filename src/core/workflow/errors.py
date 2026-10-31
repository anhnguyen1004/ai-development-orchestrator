"""Domain-specific validation errors for workflow definitions, DAG compilation, and data contracts."""

from src.core.contracts.ids import StepId


class WorkflowValidationError(Exception):
    """Base exception for all workflow definition and compilation validation errors."""


class CircularDependencyError(WorkflowValidationError):
    """Raised when a circular dependency cycle is detected in the workflow DAG."""

    def __init__(self, cycle_path: list[StepId] | tuple[StepId, ...]) -> None:
        self.cycle_path = tuple(cycle_path)
        path_str = " -> ".join(str(s) for s in self.cycle_path)
        super().__init__(f"Circular dependency cycle detected in workflow DAG: {path_str}")


class UnknownDependencyError(WorkflowValidationError):
    """Raised when a step depends on a step_id that does not exist in the workflow."""

    def __init__(self, step_id: StepId, unknown_dependency: StepId) -> None:
        self.step_id = step_id
        self.unknown_dependency = unknown_dependency
        super().__init__(
            f"Step '{step_id}' references unknown dependency '{unknown_dependency}'"
        )


class SelfDependencyError(WorkflowValidationError):
    """Raised when a step declares a dependency on itself."""

    def __init__(self, step_id: StepId) -> None:
        self.step_id = step_id
        super().__init__(f"Step '{step_id}' cannot depend on itself")


class DuplicateStepIdError(WorkflowValidationError):
    """Raised when multiple steps share the same step_id."""

    def __init__(self, step_id: StepId) -> None:
        self.step_id = step_id
        super().__init__(f"Duplicate step_id '{step_id}' detected in workflow definition")


class DuplicateOutputProducerError(WorkflowValidationError):
    """Raised when multiple steps declare the same output state key."""

    def __init__(self, output_key: str, step_ids: tuple[StepId, ...]) -> None:
        self.output_key = output_key
        self.step_ids = step_ids
        steps_str = ", ".join(f"'{s}'" for s in step_ids)
        super().__init__(
            f"State key '{output_key}' has multiple producers: {steps_str}. "
            "Every state key must have at most one producing step."
        )


class OutputOverwritesInputError(WorkflowValidationError):
    """Raised when a step output collides with a declared workflow input key."""

    def __init__(self, key: str, step_id: StepId) -> None:
        self.key = key
        self.step_id = step_id
        super().__init__(
            f"Step '{step_id}' declares output '{key}' which collides with a workflow input. "
            "Step outputs cannot overwrite workflow inputs."
        )


class UnsatisfiedStateInputError(WorkflowValidationError):
    """Raised when a step requires an input that is not produced by any ancestor or required workflow input."""

    def __init__(self, step_id: StepId, key: str, reason: str = "") -> None:
        self.step_id = step_id
        self.key = key
        self.reason = reason
        msg = f"Step '{step_id}' requires input '{key}', which is not available from required workflow inputs or ancestor steps."
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class OptionalInputAsRequiredDependencyError(WorkflowValidationError):
    """Raised when a step requires an input that is only defined as an optional workflow input."""

    def __init__(self, step_id: StepId, key: str) -> None:
        self.key = key
        self.step_id = step_id
        super().__init__(
            f"Step '{step_id}' requires input '{key}', which is declared as an optional workflow input (required=False). "
            "In MVP, steps cannot depend directly on optional workflow inputs."
        )


class DuplicateKeyDeclarationError(WorkflowValidationError):
    """Raised when duplicate keys are declared within a single step or input list."""

    def __init__(self, context: str, key: str) -> None:
        self.context = context
        self.key = key
        super().__init__(f"Duplicate key '{key}' declared in {context}")

