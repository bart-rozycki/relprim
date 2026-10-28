"""Interactions between retry, timeout, validation, rate-limit and fallback policies."""

from __future__ import annotations

import asyncio

import pytest

from relprim import (
    AttemptStatus,
    EventEmitter,
    EventType,
    ExecutionStatus,
    ExponentialBackoff,
    FallbackChainError,
    FallbackPolicy,
    InMemoryEventSink,
    OperationExecutionError,
    OperationTimeoutError,
    RateLimitPolicy,
    RetryAfterExtractionError,
    RetryPolicy,
    TimeoutPolicy,
    ValidationFailedError,
    ValidationPolicy,
    async_operation,
    fallback_chain,
    resilient,
    validation_policy,
    validator,
)


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class ProviderRateLimitError(Exception):
    def __init__(self, retry_after_seconds: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


def provider_retry_after(exception: Exception) -> float | None:
    if isinstance(exception, ProviderRateLimitError):
        return exception.retry_after_seconds

    return None


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def no_jitter(base: float = 1.0) -> ExponentialBackoff:
    return ExponentialBackoff(base_delay_seconds=base, max_delay_seconds=100, jitter=False)


def non_empty() -> ValidationPolicy[str]:
    return validation_policy(
        validator("non_empty", lambda value: bool(value.strip()), message="must not be empty")
    )


@pytest.mark.parametrize(("retries", "expected_attempts"), [(0, 1), (1, 2), (3, 4)])
async def test_decorator_retries_count_retries_after_the_initial_attempt(
    retries: int,
    expected_attempts: int,
) -> None:
    calls = 0

    # A zero-second provider hint keeps the test free of real backoff sleeps
    # without changing how many attempts `retries` allows.
    @resilient(
        retries=retries,
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=lambda exception: 0,
    )
    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ProviderRateLimitError()

    with pytest.raises(OperationExecutionError) as exc_info:
        await operation()

    report = exc_info.value.report
    assert calls == expected_attempts
    assert report.attempt_count == expected_attempts
    assert report.retry_count == retries


async def test_retry_count_excludes_the_fallback_attempt() -> None:
    sleeper = RecordingSleeper()

    async def primary() -> str:
        raise TransientError("down")

    async def backup() -> str:
        return "backup"

    result = await (
        async_operation("provider_call", primary)
        .with_retry(RetryPolicy(max_attempts=3, async_sleeper=sleeper))
        .with_fallbacks(fallback_chain(("backup", backup)))
        .run()
    )

    report = result.report
    assert report.attempt_count == 4
    assert report.retry_count == 2
    assert [attempt.fallback for attempt in report.attempts] == [False, False, False, True]
    assert report.attempts[-1].attempt_number == 4


async def test_fallback_without_retries_reports_no_retry() -> None:
    sink = InMemoryEventSink()

    async def primary() -> str:
        raise TransientError("down")

    async def backup() -> str:
        return "backup"

    result = await (
        async_operation("provider_call", primary)
        .with_fallbacks(fallback_chain(("backup", backup)))
        .with_events(EventEmitter(sinks=(sink,)))
        .run()
    )

    assert result.report.attempt_count == 2
    assert result.report.retry_count == 0
    assert result.report.retried is False

    final = (await sink.events())[-1]
    assert final.event_type is EventType.OPERATION_SUCCEEDED
    assert final.payload["retry_count"] == 0


async def test_retry_and_timeout_report_timed_out_attempts() -> None:
    sleeper = RecordingSleeper()
    calls = 0

    async def hanging() -> str:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        return "unreachable"

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", hanging)
            .with_retry(
                RetryPolicy(
                    max_attempts=2,
                    retry_on=(OperationTimeoutError,),
                    async_sleeper=sleeper,
                    backoff=no_jitter(),
                )
            )
            .with_timeout(TimeoutPolicy(seconds=0.001))
            .run()
        )

    report = exc_info.value.report
    assert calls == 2
    assert report.status is ExecutionStatus.TIMED_OUT
    assert [attempt.status for attempt in report.attempts] == [AttemptStatus.TIMED_OUT] * 2
    assert sleeper.delays == [1.0]


async def test_operation_own_timeout_error_is_a_normal_failure() -> None:
    async def client_timeout() -> str:
        raise TimeoutError("socket read timed out")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", client_timeout)
            .with_timeout(TimeoutPolicy(seconds=60))
            .run()
        )

    error = exc_info.value
    assert type(error.cause) is TimeoutError
    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempts[0].status is AttemptStatus.FAILED


async def test_validation_failures_are_retried_by_default_retry_on() -> None:
    sleeper = RecordingSleeper()
    responses = iter([" ", "ok"])

    async def operation() -> str:
        return next(responses)

    result = await (
        async_operation("provider_call", operation)
        .with_retry(RetryPolicy(max_attempts=2, async_sleeper=sleeper))
        .with_validation(non_empty())
        .run()
    )

    assert result.value == "ok"
    assert result.report.retry_count == 1
    assert result.report.attempts[0].error is not None
    assert result.report.attempts[0].error.type == "ValidationFailedError"


async def test_validation_failure_is_not_retried_when_excluded_from_retry_on() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return " "

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", operation)
            .with_retry(RetryPolicy(max_attempts=3, retry_on=(TransientError,)))
            .with_validation(non_empty())
            .run()
        )

    assert calls == 1
    assert isinstance(exc_info.value.cause, ValidationFailedError)


async def test_timeout_fallback_runs_fallback_without_the_primary_timeout() -> None:
    async def hanging() -> str:
        await asyncio.Event().wait()
        return "unreachable"

    async def slow_backup() -> str:
        # Yields several times; the 1 ms primary timeout does not apply here.
        for _ in range(3):
            await asyncio.sleep(0)
        return "backup"

    result = await (
        async_operation("provider_call", hanging)
        .with_timeout(TimeoutPolicy(seconds=0.001))
        .with_fallbacks(fallback_chain(("backup", slow_backup)))
        .run()
    )

    assert result.value == "backup"
    assert result.report.attempts[0].status is AttemptStatus.TIMED_OUT
    assert result.report.status is ExecutionStatus.SUCCEEDED


async def test_invalid_fallback_candidate_moves_to_the_next_candidate() -> None:
    sink = InMemoryEventSink()

    async def primary() -> str:
        raise TransientError("down")

    async def first_backup() -> str:
        return " "

    async def second_backup() -> str:
        return "second"

    result = await (
        async_operation("provider_call", primary)
        .with_validation(non_empty())
        .with_fallbacks(fallback_chain(("first", first_backup), ("second", second_backup)))
        .with_events(EventEmitter(sinks=(sink,)))
        .run()
    )

    assert result.value == "second"
    assert result.report.metadata["fallback_candidate_name"] == "second"
    assert result.report.metadata["fallback_candidate_index"] == 1
    assert result.report.metadata["fallback_failure_count"] == 1
    assert result.report.metadata["validation_validator_name"] == "non_empty"

    validation_events = [
        (event.event_type, event.payload["fallback_candidate_name"])
        for event in await sink.events()
        if event.event_type in {EventType.VALIDATION_FAILED, EventType.VALIDATION_SUCCEEDED}
    ]
    assert validation_events == [
        (EventType.VALIDATION_FAILED, "first"),
        (EventType.VALIDATION_SUCCEEDED, "second"),
    ]


async def test_fallback_policy_filters_the_primary_failure() -> None:
    backup_calls = 0

    async def primary() -> str:
        raise PermanentError("bug")

    async def backup() -> str:
        nonlocal backup_calls
        backup_calls += 1
        return "backup"

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", primary)
            .with_fallbacks(
                fallback_chain(
                    ("backup", backup),
                    policy=FallbackPolicy(fallback_on=(TransientError,)),
                )
            )
            .run()
        )

    assert backup_calls == 0
    assert isinstance(exc_info.value.cause, PermanentError)
    assert exc_info.value.report.metadata["fallback_used"] is False


async def test_fallback_failure_after_retries_reports_chain_error() -> None:
    async def primary() -> str:
        raise TransientError("down")

    async def backup() -> str:
        raise TransientError("backup down")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", primary)
            .with_retry(RetryPolicy(max_attempts=2, async_sleeper=RecordingSleeper()))
            .with_fallbacks(fallback_chain(("backup", backup)))
            .run()
        )

    error = exc_info.value
    assert isinstance(error.cause, FallbackChainError)
    assert error.report.attempt_count == 3
    assert error.report.retry_count == 1
    assert error.report.metadata["fallback_failed"] is True


def rate_limited_operation(retry_after_values: list[float | None]):
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1

        if retry_after_values:
            raise ProviderRateLimitError(retry_after_values.pop(0))

        return "ok"

    return operation


async def test_rate_limited_failures_are_retried_even_outside_retry_on() -> None:
    sleeper = RecordingSleeper()

    result = await (
        async_operation("provider_call", rate_limited_operation([0.5]))
        .with_retry(RetryPolicy(max_attempts=2, retry_on=(TransientError,), async_sleeper=sleeper))
        .with_rate_limit(
            RateLimitPolicy(
                rate_limit_on=(ProviderRateLimitError,),
                retry_after=provider_retry_after,
            )
        )
        .run()
    )

    assert result.value == "ok"
    assert sleeper.delays == [0.5]
    assert result.report.metadata["rate_limit_wait_seconds"] == 0.5


async def test_zero_second_provider_delay_retries_immediately() -> None:
    sleeper = RecordingSleeper()

    result = await (
        async_operation("provider_call", rate_limited_operation([0]))
        .with_retry(RetryPolicy(max_attempts=2, async_sleeper=sleeper))
        .with_rate_limit(
            RateLimitPolicy(
                rate_limit_on=(ProviderRateLimitError,),
                retry_after=provider_retry_after,
                max_wait_seconds=0,
            )
        )
        .run()
    )

    assert result.value == "ok"
    assert sleeper.delays == [0.0]


async def test_max_wait_applies_to_backoff_delay_without_provider_hint() -> None:
    sleeper = RecordingSleeper()
    sink = InMemoryEventSink()

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", rate_limited_operation([None, None]))
            .with_retry(RetryPolicy(max_attempts=3, async_sleeper=sleeper, backoff=no_jitter(5)))
            .with_rate_limit(
                RateLimitPolicy(
                    rate_limit_on=(ProviderRateLimitError,),
                    retry_after=provider_retry_after,
                    max_wait_seconds=1,
                )
            )
            .with_events(EventEmitter(sinks=(sink,)))
            .run()
        )

    report = exc_info.value.report
    assert sleeper.delays == []
    assert report.attempt_count == 1
    assert report.metadata["rate_limit_wait_exceeded"] is True
    assert report.attempts[0].metadata["rate_limit_delay_seconds"] == 5.0
    assert report.attempts[0].metadata["rate_limit_delay_source"] == "backoff"

    (exceeded,) = [
        event
        for event in await sink.events()
        if event.event_type is EventType.RATE_LIMIT_WAIT_EXCEEDED
    ]
    assert exceeded.payload["delay_seconds"] == 5.0
    assert exceeded.payload["retry_after_seconds"] is None
    assert exceeded.payload["max_wait_seconds"] == 1


async def test_rejected_wait_falls_back() -> None:
    async def backup() -> str:
        return "backup"

    result = await (
        async_operation("provider_call", rate_limited_operation([30]))
        .with_retry(RetryPolicy(max_attempts=3, async_sleeper=RecordingSleeper()))
        .with_rate_limit(
            RateLimitPolicy(
                rate_limit_on=(ProviderRateLimitError,),
                retry_after=provider_retry_after,
                max_wait_seconds=5,
            )
        )
        .with_fallbacks(fallback_chain(("backup", backup)))
        .run()
    )

    assert result.value == "backup"
    assert result.report.retry_count == 0
    assert result.report.metadata["rate_limit_wait_exceeded"] is True


async def test_rate_limit_on_last_attempt_does_not_report_wait_exceeded() -> None:
    sleeper = RecordingSleeper()
    sink = InMemoryEventSink()

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", rate_limited_operation([1, 99]))
            .with_retry(RetryPolicy(max_attempts=2, async_sleeper=sleeper))
            .with_rate_limit(
                RateLimitPolicy(
                    rate_limit_on=(ProviderRateLimitError,),
                    retry_after=provider_retry_after,
                    max_wait_seconds=5,
                )
            )
            .with_events(EventEmitter(sinks=(sink,)))
            .run()
        )

    report = exc_info.value.report
    last = report.attempts[-1]

    # Attempts were exhausted; the long provider hint was never a candidate wait.
    assert sleeper.delays == [1.0]
    assert report.metadata["rate_limit_wait_exceeded"] is False
    assert last.metadata["rate_limited"] is True
    assert last.metadata["rate_limit_retry_after_seconds"] == 99
    assert last.metadata["rate_limit_delay_seconds"] is None
    assert EventType.RATE_LIMIT_WAIT_EXCEEDED not in {
        event.event_type for event in await sink.events()
    }


async def test_non_rate_limited_failures_do_not_populate_rate_limit_delay_metadata() -> None:
    async def operation() -> str:
        raise TransientError("down")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", operation)
            .with_retry(RetryPolicy(max_attempts=2, async_sleeper=RecordingSleeper()))
            .run()
        )

    for attempt in exc_info.value.report.attempts:
        assert attempt.metadata["rate_limited"] is False
        assert attempt.metadata["rate_limit_delay_seconds"] is None
        assert attempt.metadata["rate_limit_delay_source"] is None


async def test_rate_limit_without_retry_policy_fails_after_one_attempt() -> None:
    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", rate_limited_operation([1]))
            .with_rate_limit(
                RateLimitPolicy(
                    rate_limit_on=(ProviderRateLimitError,),
                    retry_after=provider_retry_after,
                )
            )
            .run()
        )

    report = exc_info.value.report
    assert report.attempt_count == 1
    assert report.metadata["rate_limit_encountered"] is True
    assert report.metadata["rate_limit_wait_seconds"] == 0.0


@pytest.mark.parametrize(
    "extractor",
    [
        lambda exception: "soon",
        lambda exception: float("nan"),
        lambda exception: -1,
        lambda exception: 1 / 0,
    ],
)
async def test_extractor_problems_end_retries_with_extraction_error(extractor) -> None:
    sleeper = RecordingSleeper()

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", rate_limited_operation([1]))
            .with_retry(RetryPolicy(max_attempts=3, async_sleeper=sleeper))
            .with_rate_limit(
                RateLimitPolicy(rate_limit_on=(ProviderRateLimitError,), retry_after=extractor)
            )
            .run()
        )

    cause = exc_info.value.cause
    assert isinstance(cause, RetryAfterExtractionError)
    assert isinstance(cause.rate_limit_error, ProviderRateLimitError)
    assert sleeper.delays == []
    assert exc_info.value.report.attempts[0].error is not None
    assert exc_info.value.report.attempts[0].error.retryable is False
