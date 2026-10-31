"""Unit tests for error taxonomy and ExecutionError record."""

import pytest
from pydantic import ValidationError

from src.core.contracts.errors import ErrorCategory, ExecutionError


def test_error_categories():
    expected = {"TRANSIENT", "PERMANENT", "SYSTEM", "USER_CANCELLED"}
    actual = {c.value for c in ErrorCategory}
    assert actual == expected


def test_execution_error_creation():
    err = ExecutionError(
        category=ErrorCategory.TRANSIENT,
        message="Rate limit exceeded",
        code="HTTP_429",
        details={"retry_after_seconds": 15},
    )
    assert err.category == ErrorCategory.TRANSIENT
    assert err.message == "Rate limit exceeded"
    assert err.code == "HTTP_429"
    assert err.details == {"retry_after_seconds": 15}
    assert err.occurred_at is not None


def test_execution_error_immutability():
    err = ExecutionError(
        category=ErrorCategory.PERMANENT,
        message="Invalid syntax in prompt",
    )
    with pytest.raises(ValidationError):
        # Attribute assignment on frozen model must raise ValidationError
        err.message = "Attempted mutation"  # type: ignore[misc]


def test_execution_error_empty_message_rejected():
    with pytest.raises(ValidationError):
        ExecutionError(
            category=ErrorCategory.SYSTEM,
            message="   ",
        )

