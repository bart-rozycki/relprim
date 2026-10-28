from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from relprim._checks import check_exception_types, check_seconds
from relprim.errors import RetryAfterExtractionError

RetryAfterExtractor: TypeAlias = Callable[[Exception], float | None]


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of evaluating an exception against a rate-limit policy."""

    rate_limited: bool
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.rate_limited and self.retry_after_seconds is not None:
            raise ValueError("non-rate-limited decisions must not include retry_after_seconds.")

    @classmethod
    def not_rate_limited(cls) -> RateLimitDecision:
        return cls(rate_limited=False)

    @classmethod
    def rate_limited_with(
        cls,
        retry_after_seconds: float | None,
    ) -> RateLimitDecision:
        return cls(
            rate_limited=True,
            retry_after_seconds=retry_after_seconds,
        )


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Provider-aware rate-limit recovery configuration.

    The policy identifies rate-limit exceptions and optionally extracts a
    provider-supplied retry delay.

    It does not decide how many retries are allowed. Retry count remains the
    responsibility of RetryPolicy.

    `max_wait_seconds` bounds the delay selected before the next attempt,
    whether that delay came from the provider hint or from the retry policy's
    backoff. A larger selected delay skips the retry and continues into
    fallback or final failure.
    """

    rate_limit_on: tuple[type[Exception], ...]
    retry_after: RetryAfterExtractor | None = None
    max_wait_seconds: float = 60.0

    def __post_init__(self) -> None:
        check_exception_types(self.rate_limit_on, "rate_limit_on")
        check_seconds(self.max_wait_seconds, "max_wait_seconds", allow_zero=True)

    def evaluate(self, exception: Exception) -> RateLimitDecision:
        """Evaluate whether an exception represents a rate-limit response."""
        if not isinstance(exception, self.rate_limit_on):
            return RateLimitDecision.not_rate_limited()

        if self.retry_after is None:
            return RateLimitDecision.rate_limited_with(None)

        try:
            retry_after_seconds = self.retry_after(exception)
        except Exception as cause:
            raise RetryAfterExtractionError(
                "The retry-after extractor raised an exception.",
                rate_limit_error=exception,
                cause=cause,
            ) from cause

        if retry_after_seconds is None:
            return RateLimitDecision.rate_limited_with(None)

        validated_delay = _validate_retry_after(
            retry_after_seconds,
            rate_limit_error=exception,
        )

        return RateLimitDecision.rate_limited_with(validated_delay)


def rate_limit_policy(
    *,
    rate_limit_on: tuple[type[Exception], ...],
    retry_after: RetryAfterExtractor | None = None,
    max_wait_seconds: float = 60.0,
) -> RateLimitPolicy:
    """Create a provider-aware rate-limit policy."""
    return RateLimitPolicy(
        rate_limit_on=rate_limit_on,
        retry_after=retry_after,
        max_wait_seconds=max_wait_seconds,
    )


def _validate_retry_after(
    value: object,
    *,
    rate_limit_error: Exception,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RetryAfterExtractionError(
            "The retry-after extractor must return an int, float or None.",
            rate_limit_error=rate_limit_error,
        )

    retry_after_seconds = float(value)

    if not math.isfinite(retry_after_seconds):
        raise RetryAfterExtractionError(
            "The retry-after value must be finite.",
            rate_limit_error=rate_limit_error,
        )

    if retry_after_seconds < 0:
        raise RetryAfterExtractionError(
            "The retry-after value must be greater than or equal to 0.",
            rate_limit_error=rate_limit_error,
        )

    return retry_after_seconds
