"""Strict JSON validation and recursive deep freezing for workflow definitions."""

import math
import types
from typing import Any

ALLOWED_PRIMITIVE_TYPES = (bool, int, str)


def validate_json_serializable(val: Any, seen: set[int] | None = None) -> None:
    """
    Strictly validate that a value contains only JSON-compliant primitives:
    - None
    - bool
    - int
    - finite float (rejects NaN, Infinity, -Infinity)
    - str
    - list / tuple containing valid JSON values
    - dict / mappingproxy with string keys and valid JSON values

    Explicitly rejects:
    - set, bytes, datetime, custom objects
    - non-string dictionary keys (e.g. integer or tuple keys)
    - NaN, Infinity, -Infinity
    - circular references
    """
    if seen is None:
        seen = set()

    # None and primitive types (Note: bool is a subclass of int in Python)
    if val is None or isinstance(val, ALLOWED_PRIMITIVE_TYPES):
        return

    # Floats must be finite (reject NaN, Infinity)
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ValueError(f"Non-finite float '{val}' (NaN or Infinity) is not JSON-compliant")
        return

    # Check for circular reference by memory address (id)
    val_id = id(val)
    if val_id in seen:
        raise ValueError("Circular reference detected in JSON structure")

    # Sequence types
    if isinstance(val, (list, tuple)):
        seen.add(val_id)
        try:
            for item in val:
                validate_json_serializable(item, seen)
        finally:
            seen.remove(val_id)
        return

    # Mapping types
    if isinstance(val, (dict, types.MappingProxyType)):
        seen.add(val_id)
        try:
            for k, v in val.items():
                if not isinstance(k, str):
                    raise ValueError(
                        f"Dictionary key '{k}' of type '{type(k).__name__}' is invalid. "
                        "All dictionary keys must be strings."
                    )
                validate_json_serializable(v, seen)
        finally:
            seen.remove(val_id)
        return

    # Prohibited types
    raise ValueError(
        f"Type '{type(val).__name__}' is not JSON-serializable. "
        "Allowed types are None, bool, int, finite float, str, list/tuple, and dict with string keys."
    )


def freeze_value(val: Any) -> Any:
    """Recursively freeze dicts into MappingProxyType and lists into tuples."""
    if isinstance(val, (dict, types.MappingProxyType)):
        return types.MappingProxyType({k: freeze_value(v) for k, v in val.items()})
    if isinstance(val, (list, tuple)):
        return tuple(freeze_value(v) for v in val)
    return val


def unfreeze_value(val: Any) -> Any:
    """Recursively convert MappingProxyType back to dict and tuples back to lists for serialization."""
    if isinstance(val, (types.MappingProxyType, dict)):
        return {k: unfreeze_value(v) for k, v in val.items()}
    if isinstance(val, (tuple, list)):
        return [unfreeze_value(v) for v in val]
    return val

