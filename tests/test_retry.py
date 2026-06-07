from __future__ import annotations

import pytest

from relprim import ExponentialBackoff, RetryError, RetryPolicy


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def no_sleep(_: float) -> None:
    return None


async def async_no_sleep(_: float) -> None:
    return None


def make_policy(
    *,
    max_attempts: int = 3,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    backoff: ExponentialBackoff | None = None,
) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        backoff=backoff
        or ExponentialBackoff(
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=False,
        ),
        retry_on=retry_on,
        sleeper=no_sleep,
        async_sleeper=async_no_sleep,
    )


def test_run_returns_result_without_retry() -> None:
    policy = make_policy()

    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        return "ok"

    result = policy.run(operation)

    assert result == "ok"
    assert attempts == 1


def test_run_retries_until_success() -> None:
    policy = make_policy()

    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise TransientError("temporary failure")

        return "ok"

    result = policy.run(operation)

    assert result == "ok"
    assert attempts == 3


def test_run_raises_retry_error_after_max_attempts() -> None:
    policy = make_policy(retry_on=(TransientError,))

    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientError("temporary failure")

    with pytest.raises(RetryError) as exc_info:
        policy.run(operation)

    assert attempts == 3
    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value.cause, TransientError)


def test_run_does_not_retry_non_retryable_exception() -> None:
    policy = make_policy(retry_on=(TransientError,))

    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise PermanentError("do not retry")

    with pytest.raises(PermanentError):
        policy.run(operation)

    assert attempts == 1


def test_run_passes_args_and_kwargs() -> None:
    policy = make_policy(max_attempts=1)

    def operation(prefix: str, *, value: int) -> str:
        return f"{prefix}-{value}"

    result = policy.run(operation, "item", value=42)

    assert result == "item-42"


def test_backoff_without_jitter_is_deterministic() -> None:
    backoff = ExponentialBackoff(
        base_delay_seconds=0.5,
        max_delay_seconds=2.0,
        multiplier=2.0,
        jitter=False,
    )

    assert backoff.delay_for_retry(1) == 0.5
    assert backoff.delay_for_retry(2) == 1.0
    assert backoff.delay_for_retry(3) == 2.0
    assert backoff.delay_for_retry(4) == 2.0


def test_backoff_with_jitter_stays_within_expected_range() -> None:
    backoff = ExponentialBackoff(
        base_delay_seconds=1.0,
        max_delay_seconds=1.0,
        multiplier=2.0,
        jitter=True,
    )

    delay = backoff.delay_for_retry(1)

    assert 0 <= delay <= 1.0


def test_retry_policy_validates_max_attempts() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_retry_policy_requires_at_least_one_retryable_exception() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(retry_on=())


def test_backoff_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        ExponentialBackoff(base_delay_seconds=-1)

    with pytest.raises(ValueError):
        ExponentialBackoff(max_delay_seconds=-1)

    with pytest.raises(ValueError):
        ExponentialBackoff(multiplier=0.5)

    with pytest.raises(ValueError):
        ExponentialBackoff(base_delay_seconds=2, max_delay_seconds=1)


def test_backoff_rejects_invalid_retry_number() -> None:
    backoff = ExponentialBackoff(jitter=False)

    with pytest.raises(ValueError):
        backoff.delay_for_retry(0)


@pytest.mark.asyncio
async def test_run_async_returns_result_without_retry() -> None:
    policy = make_policy()

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        return "ok"

    result = await policy.run_async(operation)

    assert result == "ok"
    assert attempts == 1


@pytest.mark.asyncio
async def test_run_async_retries_until_success() -> None:
    policy = make_policy(retry_on=(TransientError,))

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise TransientError("temporary failure")

        return "ok"

    result = await policy.run_async(operation)

    assert result == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_run_async_raises_retry_error_after_max_attempts() -> None:
    policy = make_policy(max_attempts=2, retry_on=(TransientError,))
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientError("temporary failure")

    with pytest.raises(RetryError) as exc_info:
        await policy.run_async(operation)

    assert attempts == 2
    assert exc_info.value.attempts == 2
    assert isinstance(exc_info.value.cause, TransientError)


@pytest.mark.asyncio
async def test_run_async_does_not_retry_non_retryable_exception() -> None:
    policy = make_policy(retry_on=(TransientError,))

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise PermanentError("do not retry")

    with pytest.raises(PermanentError):
        await policy.run_async(operation)

    assert attempts == 1


def test_run_uses_expected_backoff_delays() -> None:
    delays: list[float] = []

    def capture_sleep(delay: float) -> None:
        delays.append(delay)

    policy = RetryPolicy(
        max_attempts=4,
        backoff=ExponentialBackoff(
            base_delay_seconds=0.5,
            max_delay_seconds=2.0,
            multiplier=2.0,
            jitter=False,
        ),
        retry_on=(TransientError,),
        sleeper=capture_sleep,
    )

    def operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(RetryError):
        policy.run(operation)

    assert delays == [0.5, 1.0, 2.0]


@pytest.mark.asyncio
async def test_run_async_uses_expected_backoff_delays() -> None:
    delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    policy = RetryPolicy(
        max_attempts=3,
        backoff=ExponentialBackoff(
            base_delay_seconds=0.25,
            max_delay_seconds=1.0,
            multiplier=2.0,
            jitter=False,
        ),
        retry_on=(TransientError,),
        async_sleeper=capture_sleep,
    )

    async def operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(RetryError):
        await policy.run_async(operation)

    assert delays == [0.25, 0.5]
