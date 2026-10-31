"""Unit tests for StepExecutor protocol, StepRegistry, and idempotency key stability."""

from typing import Any, Coroutine
import pytest

from src.core.contracts.ids import NonEmptyStr, RunId, StepId
from src.core.contracts.status import StepOutcome
from src.core.contracts.step import StepResult
from src.core.engine.errors import DuplicateCapabilityError, UnregisteredCapabilityError
from src.core.engine.step_executor import (
    InMemoryStepRegistry,
    StepExecutor,
    StepRegistry,
)
from src.core.engine.types import (
    ReadOnlyStateView,
    StepExecutionContext,
    compute_executor_idempotency_key,
)
from src.core.workflow.step_definition import Capability


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion synchronously without requiring network/socketpair."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


class MockExecutor:
    """Mock StepExecutor for testing."""

    def __init__(self, output_to_add: dict[str, object] | None = None) -> None:
        self.output_to_add = output_to_add or {"output": "ok"}
        self.call_count = 0
        self.last_context: StepExecutionContext | None = None

    async def execute(self, context: StepExecutionContext) -> StepResult[dict[str, object]]:
        self.call_count += 1
        self.last_context = context
        new_state = context.current_state.to_dict()
        new_state.update(self.output_to_add)
        return StepResult[dict[str, object]](
            outcome=StepOutcome.SUCCEEDED,
            current_state=new_state,
        )


class TestStepExecutorProtocol:
    def test_protocol_conformance(self):
        executor = MockExecutor()
        assert isinstance(executor, StepExecutor)

        class NonExecutor:
            pass

        assert not isinstance(NonExecutor(), StepExecutor)


class TestInMemoryStepRegistry:
    def test_registry_protocol_conformance(self):
        registry = InMemoryStepRegistry()
        assert isinstance(registry, StepRegistry)

    def test_register_and_resolve_success(self):
        registry = InMemoryStepRegistry()
        executor = MockExecutor()

        registry.register("requirement.analysis", executor)
        assert registry.has_capability("requirement.analysis") is True
        assert registry.resolve("requirement.analysis") is executor

    def test_resolve_unregistered_capability_raises(self):
        registry = InMemoryStepRegistry()
        with pytest.raises(UnregisteredCapabilityError) as exc_info:
            registry.resolve("data.extraction")

        assert "No executor registered for capability 'data.extraction'" in str(exc_info.value)
        assert issubclass(UnregisteredCapabilityError, LookupError)

    def test_duplicate_registration_without_overwrite_raises(self):
        registry = InMemoryStepRegistry()
        executor1 = MockExecutor()
        executor2 = MockExecutor()

        registry.register("requirement.analysis", executor1)
        with pytest.raises(DuplicateCapabilityError) as exc_info:
            registry.register("requirement.analysis", executor2, overwrite=False)

        assert "already registered" in str(exc_info.value)
        assert registry.resolve("requirement.analysis") is executor1

    def test_duplicate_registration_with_overwrite_succeeds(self):
        registry = InMemoryStepRegistry()
        executor1 = MockExecutor()
        executor2 = MockExecutor()

        registry.register("requirement.analysis", executor1)
        registry.register("requirement.analysis", executor2, overwrite=True)
        assert registry.resolve("requirement.analysis") is executor2

    def test_invalid_capability_format_rejected(self):
        registry = InMemoryStepRegistry()
        executor = MockExecutor()

        with pytest.raises(ValueError):
            registry.register("invalid_no_dot", executor)

        with pytest.raises(ValueError):
            registry.register("UPPERCASE.NOT.ALLOWED", executor)

        assert registry.has_capability("invalid_no_dot") is False

    def test_register_non_callable_executor_raises_type_error(self):
        registry = InMemoryStepRegistry()
        with pytest.raises(TypeError) as exc_info:
            registry.register("requirement.analysis", "not_an_executor")  # type: ignore[arg-type]

        assert "must implement an async 'execute' method" in str(exc_info.value)

    def test_list_and_clear_capabilities(self):
        registry = InMemoryStepRegistry()
        registry.register("b.capability", MockExecutor())
        registry.register("a.capability", MockExecutor())

        assert registry.list_capabilities() == ["a.capability", "b.capability"]
        registry.clear()
        assert registry.list_capabilities() == []
        assert registry.has_capability("a.capability") is False


class TestIdempotencyKeyStability:
    def test_key_is_stable_across_retries(self):
        """Confirm attempt_number is not in the idempotency key, so retries reuse the same key."""
        run_id = "run-uuid-100"
        step_id = "step-code-gen"
        generation = 1

        key_attempt_1 = compute_executor_idempotency_key(run_id, step_id, execution_generation=generation)
        key_attempt_2 = compute_executor_idempotency_key(run_id, step_id, execution_generation=generation)
        key_attempt_3 = compute_executor_idempotency_key(run_id, step_id, execution_generation=generation)

        assert key_attempt_1 == key_attempt_2 == key_attempt_3

    def test_key_changes_with_logical_generation(self):
        """Confirm future logical revision generation changes the key."""
        run_id = "run-uuid-100"
        step_id = "step-code-gen"

        key_gen_1 = compute_executor_idempotency_key(run_id, step_id, execution_generation=1)
        key_gen_2 = compute_executor_idempotency_key(run_id, step_id, execution_generation=2)

        assert key_gen_1 != key_gen_2


class TestStepExecution:
    def test_async_execute_returns_valid_result(self):
        executor = MockExecutor(output_to_add={"analysis_doc": "completed"})
        ctx = StepExecutionContext(
            run_id=RunId("run-1"),
            step_id=StepId("step-1"),
            step_name=NonEmptyStr("Step One"),
            capability=Capability("requirement.analysis"),
            attempt_number=1,
            executor_idempotency_key=compute_executor_idempotency_key("run-1", "step-1", 1),
            current_state=ReadOnlyStateView({"initial": "data"}),
        )

        result = run_coro(executor.execute(ctx))
        assert result.outcome == StepOutcome.SUCCEEDED
        assert result.current_state["initial"] == "data"
        assert result.current_state["analysis_doc"] == "completed"
        assert executor.call_count == 1
