from __future__ import annotations

import asyncio

import pytest

from relprim import (
    AttemptStatus,
    ExecutionStatus,
    ExponentialBackoff,
    OperationExecutionError,
    RetryPolicy,
    TimeoutPolicy,
    async_operation,
)


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


async def async_no_sleep(_: float) -> None:
    return None


def retry_policy(
    *,
    max_attempts: int = 3,
    retry_on: tuple[type[BaseException], ...] = (TransientError,),
) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        backoff=ExponentialBackoff(
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=False,
        ),
        retry_on=retry_on,
        async_sleeper=async_no_sleep,
    )


async def test_async_operation_returns_value_and_success_report() -> None:
    async def operation() -> str:
        return "ok"

    result = await async_operation("generate_summary", operation).run()

    assert result.value == "ok"
    assert result.succeeded is True
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.operation_name == "generate_summary"
    assert result.report.attempt_count == 1
    assert result.report.retry_count == 0
    assert result.report.last_attempt.status is AttemptStatus.SUCCEEDED
    assert result.report.duration_seconds >= 0


async def test_async_operation_passes_args_and_kwargs() -> None:
    async def operation(prefix: str, *, value: int) -> str:
        return f"{prefix}-{value}"

    result = await async_operation("format_value", operation).run("item", value=42)

    assert result.value == "item-42"


async def test_async_operation_retries_until_success() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise TransientError("temporary failure")

        return "ok"

    result = await (
        async_operation("unstable_call", operation).with_retry(retry_policy(max_attempts=3)).run()
    )

    assert result.value == "ok"
    assert attempts == 3
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 3
    assert result.report.retry_count == 2
    assert result.report.retried is True
    assert [attempt.status for attempt in result.report.attempts] == [
        AttemptStatus.FAILED,
        AttemptStatus.FAILED,
        AttemptStatus.SUCCEEDED,
    ]


async def test_async_operation_raises_execution_error_after_retry_exhaustion() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientError("temporary failure")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("unstable_call", operation)
            .with_retry(retry_policy(max_attempts=3))
            .run()
        )

    error = exc_info.value

    assert attempts == 3
    assert isinstance(error.cause, TransientError)
    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempt_count == 3
    assert error.report.retry_count == 2
    assert error.report.last_error is not None
    assert error.report.last_error.type == "TransientError"
    assert error.report.last_error.retryable is True


async def test_async_operation_does_not_retry_non_retryable_exception() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise PermanentError("do not retry")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("permanent_failure", operation)
            .with_retry(retry_policy(max_attempts=3, retry_on=(TransientError,)))
            .run()
        )

    error = exc_info.value

    assert attempts == 1
    assert isinstance(error.cause, PermanentError)
    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempt_count == 1
    assert error.report.retry_count == 0
    assert error.report.last_error is not None
    assert error.report.last_error.retryable is False


async def test_async_operation_applies_timeout_policy() -> None:
    async def operation() -> str:
        await asyncio.sleep(1)
        return "ok"

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("slow_call", operation).with_timeout(TimeoutPolicy(seconds=0.01)).run()
        )

    error = exc_info.value

    assert error.report.status is ExecutionStatus.TIMED_OUT
    assert error.report.attempt_count == 1
    assert error.report.last_attempt.status is AttemptStatus.TIMED_OUT
    assert error.report.last_error is not None
    assert error.report.last_error.type == "OperationTimeoutError"


async def test_async_operation_can_retry_timeouts_when_configured() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            await asyncio.sleep(1)

        return "ok"

    result = await (
        async_operation("sometimes_slow_call", operation)
        .with_timeout(TimeoutPolicy(seconds=0.01))
        .with_retry(retry_policy(max_attempts=3, retry_on=(Exception,)))
        .run()
    )

    assert result.value == "ok"
    assert attempts == 3
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 3
    assert [attempt.status for attempt in result.report.attempts] == [
        AttemptStatus.TIMED_OUT,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.SUCCEEDED,
    ]


async def test_async_operation_builder_methods_return_new_instances() -> None:
    async def operation() -> str:
        return "ok"

    base_operation = async_operation("call", operation)
    with_retry = base_operation.with_retry(retry_policy())
    with_timeout = with_retry.with_timeout(TimeoutPolicy(seconds=1))

    assert with_retry is not base_operation
    assert with_timeout is not with_retry

    result = await base_operation.run()

    assert result.value == "ok"
    assert result.report.attempt_count == 1


def test_async_operation_rejects_empty_name() -> None:
    async def operation() -> str:
        return "ok"

    with pytest.raises(ValueError):
        async_operation(" ", operation)
