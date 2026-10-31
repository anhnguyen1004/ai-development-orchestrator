"""Unit tests for RetryPolicy deterministic backoff and error classification."""

import pytest
from pydantic import ValidationError

from src.core.contracts.errors import ErrorCategory, ExecutionError
from src.core.contracts.ids import NonEmptyStr
from src.core.engine.retry import RetryPolicy


def create_error(category: ErrorCategory, message: str = "Test failure") -> ExecutionError:
    return ExecutionError(
        category=category,
        message=NonEmptyStr(message),
    )


class TestRetryPolicyEligibility:
    def test_default_policy_attributes(self):
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.initial_delay_seconds == 1.0
        assert policy.backoff_factor == 2.0
        assert policy.max_delay_seconds == 60.0
        assert policy.jitter is True
        assert policy.allow_system_retry is False
        assert policy.retryable_categories == (ErrorCategory.TRANSIENT,)

    def test_attempt_numbering_starts_at_1(self):
        policy = RetryPolicy()
        err = create_error(ErrorCategory.TRANSIENT)

        with pytest.raises(ValueError) as exc_info:
            policy.is_retryable(err, attempt=0)
        assert "Attempt must be >= 1" in str(exc_info.value)

        with pytest.raises(ValueError):
            policy.is_retryable(err, attempt=-1)

    def test_transient_retryable_until_exhausted(self):
        policy = RetryPolicy(max_attempts=3)
        err = create_error(ErrorCategory.TRANSIENT)

        assert policy.is_retryable(err, attempt=1) is True
        assert policy.is_retryable(err, attempt=2) is True
        # Attempt 3 reaches max_attempts (budget exhausted)
        assert policy.is_retryable(err, attempt=3) is False
        assert policy.is_retryable(err, attempt=4) is False

    def test_permanent_never_retryable(self):
        policy = RetryPolicy(max_attempts=5)
        err = create_error(ErrorCategory.PERMANENT)

        assert policy.is_retryable(err, attempt=1) is False
        assert policy.is_retryable(err, attempt=2) is False

    def test_user_cancelled_never_retryable(self):
        policy = RetryPolicy(max_attempts=5)
        err = create_error(ErrorCategory.USER_CANCELLED)

        assert policy.is_retryable(err, attempt=1) is False

    def test_system_error_handling(self):
        # Default: SYSTEM is non-retryable
        default_policy = RetryPolicy(max_attempts=3)
        err = create_error(ErrorCategory.SYSTEM)
        assert default_policy.is_retryable(err, attempt=1) is False

        # Configured: allow_system_retry=True permits retrying SYSTEM
        system_policy = RetryPolicy(max_attempts=3, allow_system_retry=True)
        assert system_policy.is_retryable(err, attempt=1) is True
        assert system_policy.is_retryable(err, attempt=2) is True
        assert system_policy.is_retryable(err, attempt=3) is False  # Budget exhausted


class TestRetryPolicyBackoffCalculation:
    def test_attempt_must_be_ge_1(self):
        policy = RetryPolicy()
        with pytest.raises(ValueError):
            policy.calculate_delay(attempt=0)

    def test_exponential_backoff_without_jitter(self):
        policy = RetryPolicy(
            initial_delay_seconds=1.0,
            backoff_factor=2.0,
            max_delay_seconds=60.0,
            jitter=False,
        )

        assert policy.calculate_delay(attempt=1) == 1.0  # 1.0 * (2^0)
        assert policy.calculate_delay(attempt=2) == 2.0  # 1.0 * (2^1)
        assert policy.calculate_delay(attempt=3) == 4.0  # 1.0 * (2^2)
        assert policy.calculate_delay(attempt=4) == 8.0  # 1.0 * (2^3)

    def test_max_delay_ceiling_enforced(self):
        policy = RetryPolicy(
            initial_delay_seconds=10.0,
            backoff_factor=2.0,
            max_delay_seconds=25.0,
            jitter=False,
        )

        assert policy.calculate_delay(attempt=1) == 10.0
        assert policy.calculate_delay(attempt=2) == 20.0
        assert policy.calculate_delay(attempt=3) == 25.0  # Capped at 25.0 (40.0 > 25.0)
        assert policy.calculate_delay(attempt=10) == 25.0

    def test_jitter_input_scaling(self):
        policy = RetryPolicy(
            initial_delay_seconds=2.0,
            backoff_factor=2.0,
            jitter=True,
        )
        # Attempt 1 base_delay = 2.0
        # multiplier = 0.5 + jitter_input

        # Minimum jitter factor (0.0 -> 0.5x)
        assert policy.calculate_delay(attempt=1, jitter_input=0.0) == 1.0

        # Midpoint jitter factor (0.5 -> 1.0x)
        assert policy.calculate_delay(attempt=1, jitter_input=0.5) == 2.0

        # Maximum jitter factor (1.0 -> 1.5x)
        assert policy.calculate_delay(attempt=1, jitter_input=1.0) == 3.0

    def test_rejects_out_of_bound_jitter_input(self):
        policy = RetryPolicy(jitter=True)

        with pytest.raises(ValueError) as exc_neg:
            policy.calculate_delay(attempt=1, jitter_input=-0.01)
        assert "jitter_input must be within [0.0, 1.0]" in str(exc_neg.value)

        with pytest.raises(ValueError) as exc_pos:
            policy.calculate_delay(attempt=1, jitter_input=1.01)
        assert "jitter_input must be within [0.0, 1.0]" in str(exc_pos.value)

    def test_model_immutability(self):
        policy = RetryPolicy()
        with pytest.raises(ValidationError):
            policy.max_attempts = 10  # type: ignore[misc]

