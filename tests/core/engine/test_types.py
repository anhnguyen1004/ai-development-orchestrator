"""Unit tests for engine runtime types, ReadOnlyStateView, and StepExecutionContext."""

import math
import pytest
from pydantic import ValidationError

from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.ids import NonEmptyStr, RunId, StepId
from src.core.engine.types import (
    ArtifactStore,
    ReadOnlyStateView,
    StepExecutionContext,
    compute_executor_idempotency_key,
)
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import Capability


class TestReadOnlyStateView:
    def test_init_from_dict_and_workflow_state(self):
        raw = {"k1": "v1", "nested": {"n1": 10}, "items": [1, 2]}
        ro_dict = ReadOnlyStateView(raw, schema_id="custom.schema")
        assert ro_dict.schema_id == "custom.schema"
        assert ro_dict["k1"] == "v1"

        ws = WorkflowState[dict[str, object]](schema_id="ws.schema", data=raw)
        ro_ws = ReadOnlyStateView(ws)
        assert ro_ws.schema_id == "ws.schema"
        assert ro_ws["k1"] == "v1"
        assert ro_ws["nested"]["n1"] == 10
        assert ro_ws["items"] == (1, 2)

    def test_blocks_top_level_mutation(self):
        ro = ReadOnlyStateView({"key": "initial"})
        with pytest.raises(TypeError):
            ro["key"] = "modified"  # type: ignore[index]

        with pytest.raises(TypeError):
            ro.data["key"] = "modified"  # type: ignore[index]

    def test_blocks_nested_mapping_mutation(self):
        ro = ReadOnlyStateView({"nested": {"k": 1, "deep": {"inner": True}}})
        with pytest.raises(TypeError):
            ro["nested"]["k"] = 999  # type: ignore[index]

        with pytest.raises(TypeError):
            ro["nested"]["deep"]["inner"] = False  # type: ignore[index]

    def test_blocks_nested_list_mutation(self):
        ro = ReadOnlyStateView({"items": [10, 20, [30, 40]]})
        with pytest.raises(TypeError):
            ro["items"][0] = 999  # type: ignore[index]

        with pytest.raises(TypeError):
            ro["items"][2][0] = 999  # type: ignore[index]

    def test_to_dict_returns_independent_mutable_copy(self):
        original = {"a": 1, "nested": {"b": 2}, "list": [3, 4]}
        ro = ReadOnlyStateView(original)

        copy_dict = ro.to_dict()
        assert copy_dict == original
        assert isinstance(copy_dict, dict)
        assert isinstance(copy_dict["nested"], dict)
        assert isinstance(copy_dict["list"], list)

        # Mutate the copy
        copy_dict["a"] = 999
        copy_dict["nested"]["b"] = 888
        copy_dict["list"].append(5)

        # Verify ReadOnlyStateView remains pristine
        assert ro["a"] == 1
        assert ro["nested"]["b"] == 2
        assert ro["list"] == (3, 4)

    def test_mapping_interface(self):
        data = {"x": 10, "y": 20}
        ro = ReadOnlyStateView(data)
        assert len(ro) == 2
        assert list(iter(ro)) == ["x", "y"]
        assert list(ro.keys()) == ["x", "y"]
        assert list(ro.values()) == [10, 20]
        assert list(ro.items()) == [("x", 10), ("y", 20)]
        assert ro.get("x") == 10
        assert ro.get("z", 99) == 99
        assert "x" in ro
        assert "z" not in ro

    def test_equality_and_repr(self):
        ro1 = ReadOnlyStateView({"a": 1})
        ro2 = ReadOnlyStateView({"a": 1})
        ro3 = ReadOnlyStateView({"a": 2})

        assert ro1 == ro2
        assert ro1 != ro3
        assert ro1 == {"a": 1}
        assert repr(ro1).startswith("ReadOnlyStateView(")

    def test_rejects_non_json_serializable(self):
        with pytest.raises(ValueError):
            ReadOnlyStateView({"nan": float("nan")})

        with pytest.raises(ValueError):
            ReadOnlyStateView({"inf": float("inf")})

        with pytest.raises(ValueError):
            ReadOnlyStateView({"set": {1, 2}})


class TestStepExecutionContext:
    def test_valid_context_creation(self):
        ro = ReadOnlyStateView({"num": 42})
        ctx = StepExecutionContext(
            run_id=RunId("run-100"),
            step_id=StepId("step-analysis"),
            step_name=NonEmptyStr("Requirement Analysis"),
            capability=Capability("requirement.analysis"),
            inputs={"input_key": "val1"},
            config={"timeout_sec": 30},
            attempt_number=1,
            executor_idempotency_key="hash123",
            current_state=ro,
        )

        assert ctx.run_id == "run-100"
        assert ctx.step_id == "step-analysis"
        assert ctx.step_name == "Requirement Analysis"
        assert ctx.capability == "requirement.analysis"
        assert ctx.inputs["input_key"] == "val1"
        assert ctx.config["timeout_sec"] == 30
        assert ctx.attempt_number == 1
        assert ctx.current_state["num"] == 42

    def test_context_immutability(self):
        ro = ReadOnlyStateView({"num": 42})
        ctx = StepExecutionContext(
            run_id=RunId("run-100"),
            step_id=StepId("step-analysis"),
            step_name=NonEmptyStr("Analysis"),
            capability=Capability("requirement.analysis"),
            attempt_number=1,
            executor_idempotency_key="hash123",
            current_state=ro,
        )

        with pytest.raises(ValidationError):
            ctx.attempt_number = 2  # type: ignore[misc]

    def test_inputs_and_config_are_frozen(self):
        ro = ReadOnlyStateView({"num": 42})
        ctx = StepExecutionContext(
            run_id=RunId("run-100"),
            step_id=StepId("step-analysis"),
            step_name=NonEmptyStr("Analysis"),
            capability=Capability("requirement.analysis"),
            inputs={"a": {"nested": 1}},
            config={"b": [1, 2]},
            attempt_number=1,
            executor_idempotency_key="hash123",
            current_state=ro,
        )

        with pytest.raises(TypeError):
            ctx.inputs["a"]["nested"] = 999  # type: ignore[index]

        with pytest.raises(TypeError):
            ctx.config["b"][0] = 999  # type: ignore[index]

    def test_serialization(self):
        ro = ReadOnlyStateView({"x": 100})
        ctx = StepExecutionContext(
            run_id=RunId("run-100"),
            step_id=StepId("step-analysis"),
            step_name=NonEmptyStr("Analysis"),
            capability=Capability("requirement.analysis"),
            inputs={"in_val": "data"},
            config={"retries": 3},
            attempt_number=1,
            executor_idempotency_key="abc",
            current_state=ro,
        )

        dumped = ctx.model_dump()
        assert dumped["run_id"] == "run-100"
        assert dumped["current_state"] == {"schema_id": "dict[str, Any]", "data": {"x": 100}}
        assert dumped["inputs"] == {"in_val": "data"}
        assert dumped["config"] == {"retries": 3}

    def test_attempt_number_must_be_ge_1(self):
        ro = ReadOnlyStateView({})
        with pytest.raises(ValidationError):
            StepExecutionContext(
                run_id=RunId("run-100"),
                step_id=StepId("step-analysis"),
                step_name=NonEmptyStr("Analysis"),
                capability=Capability("requirement.analysis"),
                attempt_number=0,  # Invalid: ge=1 required
                executor_idempotency_key="hash123",
                current_state=ro,
            )


class TestIdempotencyKey:
    def test_idempotency_key_deterministic(self):
        k1 = compute_executor_idempotency_key("run-1", "step-a", 1)
        k2 = compute_executor_idempotency_key("run-1", "step-a", 1)
        assert k1 == k2
        assert len(k1) == 64

    def test_idempotency_key_changes_with_parameters(self):
        k1 = compute_executor_idempotency_key("run-1", "step-a", 1)
        k2 = compute_executor_idempotency_key("run-2", "step-a", 1)
        k3 = compute_executor_idempotency_key("run-1", "step-b", 1)
        k4 = compute_executor_idempotency_key("run-1", "step-a", 2)

        assert k1 != k2
        assert k1 != k3
        assert k1 != k4


class TestArtifactStoreProtocol:
    def test_artifact_store_runtime_checkable(self):
        class DummyArtifactStore:
            async def verify_artifact(self, ref: ArtifactRef) -> bool:
                return True

        assert isinstance(DummyArtifactStore(), ArtifactStore)

        class IncompleteStore:
            pass

        assert not isinstance(IncompleteStore(), ArtifactStore)

