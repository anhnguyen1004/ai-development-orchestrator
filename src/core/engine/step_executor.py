"""StepExecutor protocol and Capability-based StepRegistry."""

from typing import Any, Protocol, runtime_checkable
from src.core.contracts.step import StepResult
from src.core.engine.errors import DuplicateCapabilityError, UnregisteredCapabilityError
from src.core.engine.types import StepExecutionContext
from src.core.workflow.step_definition import CAPABILITY_REGEX, Capability


@runtime_checkable
class StepExecutor(Protocol):
    """
    Stateless executor protocol capable of performing a single capability.

    Decoupled from LLM providers, agent personas, and business applications.
    """

    async def execute(self, context: StepExecutionContext) -> StepResult[dict[str, Any]]:
        """
        Execute work for the given context and return a validated StepResult.

        Must return a complete state snapshot in current_state if SUCCEEDED.
        Must not mutate context.current_state.
        """
        ...


@runtime_checkable
class StepRegistry(Protocol):
    """Protocol for resolving abstract Capabilities to concrete StepExecutors."""

    def register(
        self,
        capability: Capability | str,
        executor: StepExecutor,
        overwrite: bool = False,
    ) -> None:
        """Register a StepExecutor for a capability."""
        ...

    def resolve(self, capability: Capability | str) -> StepExecutor:
        """Resolve a StepExecutor for a capability, or raise UnregisteredCapabilityError."""
        ...

    def has_capability(self, capability: Capability | str) -> bool:
        """Check whether an executor is registered for a capability."""
        ...


class InMemoryStepRegistry:
    """In-memory capability-based StepRegistry."""

    def __init__(self) -> None:
        self._executors: dict[str, StepExecutor] = {}

    def _normalize_capability(self, capability: Capability | str) -> str:
        cap_str = str(capability)
        if not CAPABILITY_REGEX.match(cap_str):
            raise ValueError(
                f"Invalid capability format: '{cap_str}'. Must be lowercase dot-separated (e.g. 'requirement.analysis')."
            )
        return cap_str

    def register(
        self,
        capability: Capability | str,
        executor: StepExecutor,
        overwrite: bool = False,
    ) -> None:
        """
        Register a StepExecutor for a capability.

        Raises DuplicateCapabilityError if capability is already registered and overwrite is False.
        Raises TypeError if executor does not implement StepExecutor protocol.
        """
        cap_str = self._normalize_capability(capability)

        if not hasattr(executor, "execute") or not callable(getattr(executor, "execute")):
            raise TypeError(f"Executor for '{cap_str}' must implement an async 'execute' method")

        if not overwrite and cap_str in self._executors:
            raise DuplicateCapabilityError(f"Capability '{cap_str}' is already registered")

        self._executors[cap_str] = executor

    def resolve(self, capability: Capability | str) -> StepExecutor:
        """
        Resolve a StepExecutor for a capability.

        Raises UnregisteredCapabilityError if capability is not registered.
        """
        cap_str = self._normalize_capability(capability)
        if cap_str not in self._executors:
            raise UnregisteredCapabilityError(
                f"No executor registered for capability '{cap_str}'"
            )
        return self._executors[cap_str]

    def has_capability(self, capability: Capability | str) -> bool:
        """Check whether an executor is registered for a capability."""
        try:
            cap_str = self._normalize_capability(capability)
            return cap_str in self._executors
        except ValueError:
            return False

    def list_capabilities(self) -> list[str]:
        """Return sorted list of registered capability strings."""
        return sorted(self._executors.keys())

    def clear(self) -> None:
        """Clear all registered executors."""
        self._executors.clear()

