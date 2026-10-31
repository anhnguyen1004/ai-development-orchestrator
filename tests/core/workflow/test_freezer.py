"""Unit tests for strict JSON validation and deep freezing logic."""

from datetime import datetime
import pytest

from src.core.workflow.freezer import freeze_value, unfreeze_value, validate_json_serializable


def test_accepted_primitives_and_structures():
    valid_payload = {
        "null_val": None,
        "bool_val": True,
        "int_val": 42,
        "float_val": 3.14159,
        "str_val": "hello world",
        "nested_list": [1, "two", False, None],
        "nested_dict": {"k": "v", "inner": [10, 20]},
        "empty_dict": {},
        "empty_list": [],
    }
    # Should not raise
    validate_json_serializable(valid_payload)


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
)
def test_rejected_non_finite_floats(non_finite: float):
    with pytest.raises(ValueError, match="Non-finite float"):
        validate_json_serializable({"val": non_finite})


@pytest.mark.parametrize(
    "invalid_key_dict",
    [{1: "a"}, {(1, 2): "b"}, {None: "c"}],
)
def test_rejected_non_string_dict_keys(invalid_key_dict: dict):
    with pytest.raises(ValueError, match="All dictionary keys must be strings"):
        validate_json_serializable(invalid_key_dict)


@pytest.mark.parametrize(
    "invalid_type",
    [
        {1, 2, 3},               # set
        b"raw bytes",            # bytes
        datetime.now(),          # datetime
        object(),                # custom object
    ],
)
def test_rejected_non_json_types(invalid_type):
    with pytest.raises(ValueError, match="is not JSON-serializable"):
        validate_json_serializable({"data": invalid_type})


def test_rejected_circular_reference_dict():
    circ: dict = {"name": "test"}
    circ["self"] = circ
    with pytest.raises(ValueError, match="Circular reference detected"):
        validate_json_serializable(circ)


def test_rejected_circular_reference_list():
    circ: list = [1, 2]
    circ.append(circ)
    with pytest.raises(ValueError, match="Circular reference detected"):
        validate_json_serializable(circ)


def test_rejected_indirect_circular_reference():
    a: dict = {"key": "val"}
    b: list = [a]
    a["link"] = b
    with pytest.raises(ValueError, match="Circular reference detected"):
        validate_json_serializable(a)


def test_deep_freezing_and_unfreezing():
    raw = {
        "str": "text",
        "list": [1, {"nested_in_list": 2}],
        "dict": {"a": [3, 4]},
    }
    frozen = freeze_value(raw)

    # Top-level dict mutation rejected
    with pytest.raises(TypeError):
        frozen["str"] = "mutated"  # type: ignore[index]

    # Nested dict in dict mutation rejected
    with pytest.raises(TypeError):
        frozen["dict"]["a"] = [5]  # type: ignore[index]

    # Nested list in dict is a tuple, mutation rejected
    with pytest.raises(AttributeError):
        frozen["dict"]["a"].append(5)  # type: ignore[attr-defined]

    # Unfreeze restores native mutable dict and list
    restored = unfreeze_value(frozen)
    assert restored == raw
    assert isinstance(restored, dict)
    assert isinstance(restored["list"], list)
    restored["str"] = "can_mutate_now"
    assert restored["str"] == "can_mutate_now"

