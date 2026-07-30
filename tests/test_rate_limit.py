from __future__ import annotations

import pytest

from relprim import (
    RateLimitPolicy,
    RetryAfterExtractionError,
    rate_limit_policy,
)


class ProviderRateLimitError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderError(Exception):
    pass


def extract_retry_after(exception: Exception) -> float | None:
    if isinstance(exception, ProviderRateLimitError):
        return exception.retry_after_seconds

    return None


def test_rate_limit_policy_detects_matching_exception() -> None:
    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
    )

    decision = policy.evaluate(ProviderRateLimitError("too many requests"))

    assert decision.rate_limited is True
    assert decision.retry_after_seconds is None


def test_rate_limit_policy_ignores_non_matching_exception() -> None:
    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
    )

    decision = policy.evaluate(ProviderError("provider failed"))

    assert decision.rate_limited is False
    assert decision.retry_after_seconds is None


def test_rate_limit_policy_extracts_provider_delay() -> None:
    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=extract_retry_after,
        max_wait_seconds=30,
    )

    decision = policy.evaluate(
        ProviderRateLimitError(
            "too many requests",
            retry_after_seconds=2.5,
        )
    )

    assert decision.rate_limited is True
    assert decision.retry_after_seconds == 2.5


def test_rate_limit_policy_accepts_immediate_retry() -> None:
    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=extract_retry_after,
    )

    decision = policy.evaluate(
        ProviderRateLimitError(
            "too many requests",
            retry_after_seconds=0,
        )
    )

    assert decision.retry_after_seconds == 0


def test_rate_limit_policy_allows_missing_provider_delay() -> None:
    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=extract_retry_after,
    )

    decision = policy.evaluate(
        ProviderRateLimitError(
            "too many requests",
            retry_after_seconds=None,
        )
    )

    assert decision.rate_limited is True
    assert decision.retry_after_seconds is None


def test_rate_limit_policy_rejects_empty_exception_types() -> None:
    with pytest.raises(
        ValueError,
        match="rate_limit_on must contain at least one exception type",
    ):
        RateLimitPolicy(rate_limit_on=())


@pytest.mark.parametrize("max_wait_seconds", [-1, -0.1])
def test_rate_limit_policy_rejects_negative_max_wait(
    max_wait_seconds: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_wait_seconds must be greater than or equal to 0",
    ):
        RateLimitPolicy(
            rate_limit_on=(ProviderRateLimitError,),
            max_wait_seconds=max_wait_seconds,
        )


@pytest.mark.parametrize(
    "retry_after_seconds",
    [
        -1,
        float("inf"),
        float("-inf"),
        float("nan"),
    ],
)
def test_rate_limit_policy_rejects_invalid_retry_after(
    retry_after_seconds: float,
) -> None:
    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=extract_retry_after,
    )

    with pytest.raises(RetryAfterExtractionError):
        policy.evaluate(
            ProviderRateLimitError(
                "too many requests",
                retry_after_seconds=retry_after_seconds,
            )
        )


def test_rate_limit_policy_wraps_extractor_failure() -> None:
    extractor_error = RuntimeError("broken extractor")

    def broken_extractor(exception: Exception) -> float | None:
        raise extractor_error

    rate_limit_error = ProviderRateLimitError("too many requests")

    policy = rate_limit_policy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=broken_extractor,
    )

    with pytest.raises(RetryAfterExtractionError) as exc_info:
        policy.evaluate(rate_limit_error)

    assert exc_info.value.rate_limit_error is rate_limit_error
    assert exc_info.value.cause is extractor_error
