"""Deterministic, bounded retry policy and backoff calculation."""

from pydantic import BaseModel, ConfigDict, Field
from src.core.contracts.errors import ErrorCategory, ExecutionError


class RetryPolicy(BaseModel):
    """
    Deterministic, bounded retry configuration.

    Separates error taxonomy from execution retry policy. Computes exponential backoff
    with strictly validated jitter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(ge=1, default=3, description="Maximum execution attempts (>= 1)")
    initial_delay_seconds: float = Field(ge=0.0, default=1.0, description="Initial backoff delay in seconds")
    backoff_factor: float = Field(ge=1.0, default=2.0, description="Exponential multiplier")
    max_delay_seconds: float = Field(ge=0.0, default=60.0, description="Maximum ceiling delay in seconds")
    jitter: bool = Field(default=True, description="Whether to apply jitter scaling")
    allow_system_retry: bool = Field(
        default=False,
        description="Whether to permit retrying ErrorCategory.SYSTEM errors",
    )
    retryable_categories: tuple[ErrorCategory, ...] = Field(
        default=(ErrorCategory.TRANSIENT,),
        description="Categories eligible for retry",
    )

    def is_retryable(self, error: ExecutionError, attempt: int) -> bool:
        """
        Evaluate whether an error on a specific attempt is eligible for retry.

        Rules:
        - attempt must be >= 1.
        - attempt >= max_attempts returns False (budget exhausted).
        - USER_CANCELLED is strictly non-retryable.
        - PERMANENT is strictly non-retryable.
        - SYSTEM is non-retryable unless allow_system_retry is True.
        - Other categories match against retryable_categories.
        """
        if attempt < 1:
            raise ValueError(f"Attempt must be >= 1, got {attempt}")

        if attempt >= self.max_attempts:
            return False

        if error.category == ErrorCategory.USER_CANCELLED:
            return False

        if error.category == ErrorCategory.PERMANENT:
            return False

        if error.category == ErrorCategory.SYSTEM:
            return self.allow_system_retry

        return error.category in self.retryable_categories

    def calculate_delay(self, attempt: int, jitter_input: float = 0.5) -> float:
        """
        Calculate deterministic exponential backoff delay.

        delay = min(initial_delay * (backoff_factor ** (attempt - 1)), max_delay)

        When jitter=True, jitter_input must be strictly in [0.0, 1.0].
        The resulting delay is scaled by (0.5 + jitter_input), yielding a multiplier in [0.5, 1.5].
        """
        if attempt < 1:
            raise ValueError(f"Attempt must be >= 1, got {attempt}")

        base_delay = min(
            self.initial_delay_seconds * (self.backoff_factor ** (attempt - 1)),
            self.max_delay_seconds,
        )

        if not self.jitter:
            return base_delay

        if not (0.0 <= jitter_input <= 1.0):
            raise ValueError(f"jitter_input must be within [0.0, 1.0], got {jitter_input}")

        multiplier = 0.5 + jitter_input
        return base_delay * multiplier

