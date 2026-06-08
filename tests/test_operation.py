from __future__ import annotations

import asyncio

import pytest

from relprim import (
    AttemptStatus,
    CircuitBreaker,
    CircuitBreakerOpenError,
    EventEmitter,
    EventType,
    ExecutionStatus,
    ExponentialBackoff,
    InMemoryEventSink,
    OperationExecutionError,
    RetryPolicy,
    TimeoutPolicy,
    ValidationFailedError,
    async_operation,
    fallback_chain,
    validation_policy,
    validator,
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


async def test_async_operation_uses_fallback_after_primary_failure() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def fallback() -> str:
        return "fallback-result"

    result = await (
        async_operation("provider_call", primary)
        .with_retry(retry_policy(max_attempts=1))
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .run()
    )

    assert result.value == "fallback-result"
    assert result.succeeded is True
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 2
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_failed"] is False
    assert result.report.metadata["fallback_candidate_name"] == "fallback"
    assert result.report.metadata["fallback_candidate_index"] == 0
    assert result.report.metadata["fallback_failure_count"] == 0
    assert result.report.attempts[0].status is AttemptStatus.FAILED
    assert result.report.attempts[0].metadata["fallback_used"] is False
    assert result.report.attempts[1].status is AttemptStatus.SUCCEEDED
    assert result.report.attempts[1].metadata["fallback_used"] is True


async def test_async_operation_uses_fallback_after_retry_exhaustion() -> None:
    attempts = 0

    async def primary() -> str:
        nonlocal attempts
        attempts += 1
        raise TransientError("primary unavailable")

    async def fallback() -> str:
        return "fallback-result"

    result = await (
        async_operation("provider_call", primary)
        .with_retry(retry_policy(max_attempts=3))
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .run()
    )

    assert result.value == "fallback-result"
    assert attempts == 3
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 4
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_failed"] is False
    assert result.report.metadata["fallback_candidate_name"] == "fallback"
    assert [attempt.status for attempt in result.report.attempts] == [
        AttemptStatus.FAILED,
        AttemptStatus.FAILED,
        AttemptStatus.FAILED,
        AttemptStatus.SUCCEEDED,
    ]


async def test_async_operation_fallback_chain_preserves_fallback_failure_count() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def first_fallback() -> str:
        raise TransientError("first fallback unavailable")

    async def second_fallback() -> str:
        return "second-fallback-result"

    result = await (
        async_operation("provider_call", primary)
        .with_retry(retry_policy(max_attempts=1))
        .with_fallbacks(
            fallback_chain(
                ("first_fallback", first_fallback),
                ("second_fallback", second_fallback),
            )
        )
        .run()
    )

    assert result.value == "second-fallback-result"
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_failed"] is False
    assert result.report.metadata["fallback_candidate_name"] == "second_fallback"
    assert result.report.metadata["fallback_candidate_index"] == 1
    assert result.report.metadata["fallback_failure_count"] == 1


async def test_async_operation_raises_execution_error_when_primary_and_fallbacks_fail() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def fallback() -> str:
        raise TransientError("fallback unavailable")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", primary)
            .with_retry(retry_policy(max_attempts=1))
            .with_fallbacks(
                fallback_chain(
                    ("fallback", fallback),
                )
            )
            .run()
        )

    error = exc_info.value

    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempt_count == 2
    assert error.report.metadata["fallback_used"] is True
    assert error.report.metadata["fallback_failed"] is True
    assert error.report.metadata["fallback_failure_count"] == 1
    assert error.report.attempts[0].status is AttemptStatus.FAILED
    assert error.report.attempts[0].metadata["fallback_used"] is False
    assert error.report.attempts[1].status is AttemptStatus.FAILED
    assert error.report.attempts[1].metadata["fallback_used"] is True
    assert error.report.last_error is not None
    assert error.report.last_error.type == "FallbackChainError"


async def test_async_operation_does_not_use_fallback_when_primary_succeeds() -> None:
    fallback_called = False

    async def primary() -> str:
        return "primary-result"

    async def fallback() -> str:
        nonlocal fallback_called
        fallback_called = True
        return "fallback-result"

    result = await (
        async_operation("provider_call", primary)
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .run()
    )

    assert result.value == "primary-result"
    assert fallback_called is False
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 1
    assert result.report.metadata["fallback_used"] is False


async def test_async_operation_builder_preserves_fallbacks_across_policy_changes() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def fallback() -> str:
        return "fallback-result"

    operation = (
        async_operation("provider_call", primary)
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .with_retry(retry_policy(max_attempts=1))
        .with_timeout(TimeoutPolicy(seconds=1))
    )

    result = await operation.run()

    assert result.value == "fallback-result"
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_candidate_name"] == "fallback"


async def test_async_operation_uses_circuit_breaker() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        return "ok"

    result = await async_operation("provider_call", operation).with_circuit_breaker(breaker).run()

    snapshot = await breaker.snapshot()

    assert result.value == "ok"
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.metadata["circuit_breaker_open"] is False
    assert snapshot.closed is True


async def test_async_operation_records_circuit_breaker_open_failure() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        raise TransientError("provider unavailable")

    with pytest.raises(OperationExecutionError):
        await async_operation("provider_call", operation).with_circuit_breaker(breaker).run()

    with pytest.raises(OperationExecutionError) as exc_info:
        await async_operation("provider_call", operation).with_circuit_breaker(breaker).run()

    error = exc_info.value

    assert isinstance(error.cause, CircuitBreakerOpenError)
    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempt_count == 1
    assert error.report.metadata["circuit_breaker_open"] is True
    assert error.report.last_attempt.metadata["circuit_breaker_open"] is True
    assert error.report.last_error is not None
    assert error.report.last_error.type == "CircuitBreakerOpenError"


async def test_async_operation_can_retry_until_circuit_breaker_opens() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=2,
        record_failure_on=(TransientError,),
    )

    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise TransientError("provider unavailable")

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", operation)
            .with_retry(
                retry_policy(
                    max_attempts=3,
                    retry_on=(TransientError, CircuitBreakerOpenError),
                )
            )
            .with_circuit_breaker(breaker)
            .run()
        )

    error = exc_info.value

    assert calls == 2
    assert isinstance(error.cause, CircuitBreakerOpenError)
    assert error.report.attempt_count == 3
    assert [
        attempt.error.type if attempt.error is not None else None
        for attempt in error.report.attempts
    ] == [
        "TransientError",
        "TransientError",
        "CircuitBreakerOpenError",
    ]
    assert error.report.last_attempt.metadata["circuit_breaker_open"] is True


async def test_async_operation_uses_fallback_when_circuit_breaker_is_open() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        record_failure_on=(TransientError,),
    )

    async def primary() -> str:
        raise TransientError("provider unavailable")

    async def fallback() -> str:
        return "fallback-result"

    with pytest.raises(OperationExecutionError):
        await async_operation("provider_call", primary).with_circuit_breaker(breaker).run()

    result = await (
        async_operation("provider_call", primary)
        .with_circuit_breaker(breaker)
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .run()
    )

    assert result.value == "fallback-result"
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_candidate_name"] == "fallback"
    assert result.report.attempts[0].metadata["circuit_breaker_open"] is True
    assert result.report.attempts[1].metadata["fallback_used"] is True


async def test_async_operation_builder_preserves_circuit_breaker_across_policy_changes() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        return "ok"

    configured_operation = (
        async_operation("provider_call", operation)
        .with_circuit_breaker(breaker)
        .with_retry(retry_policy(max_attempts=2))
        .with_timeout(TimeoutPolicy(seconds=1))
    )

    result = await configured_operation.run()
    snapshot = await breaker.snapshot()

    assert result.value == "ok"
    assert result.report.metadata["circuit_breaker_open"] is False
    assert snapshot.closed is True


async def test_async_operation_validates_successful_result() -> None:
    async def operation() -> str:
        return "valid response"

    result = await (
        async_operation("provider_call", operation)
        .with_validation(
            validation_policy(
                validator(
                    "non_empty",
                    lambda value: bool(value.strip()),
                    message="Value must not be empty.",
                )
            )
        )
        .run()
    )

    assert result.value == "valid response"
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.metadata["validation_performed"] is True
    assert result.report.metadata["validation_valid"] is True
    assert result.report.metadata["validation_validator_name"] == "validation_policy"
    assert result.report.metadata["validation_reason"] is None
    assert result.report.last_attempt.metadata["validation_valid"] is True


async def test_async_operation_raises_execution_error_when_validation_fails() -> None:
    async def operation() -> str:
        return " "

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", operation)
            .with_validation(
                validation_policy(
                    validator(
                        "non_empty",
                        lambda value: bool(value.strip()),
                        message="Value must not be empty.",
                    )
                )
            )
            .run()
        )

    error = exc_info.value

    assert isinstance(error.cause, ValidationFailedError)
    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempt_count == 1
    assert error.report.metadata["validation_performed"] is True
    assert error.report.metadata["validation_valid"] is False
    assert error.report.metadata["validation_validator_name"] == "non_empty"
    assert error.report.metadata["validation_reason"] == "Value must not be empty."
    assert error.report.last_error is not None
    assert error.report.last_error.type == "ValidationFailedError"


async def test_async_operation_retries_validation_failure_when_configured() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1

        if calls < 3:
            return " "

        return "valid response"

    result = await (
        async_operation("provider_call", operation)
        .with_retry(
            retry_policy(
                max_attempts=3,
                retry_on=(ValidationFailedError,),
            )
        )
        .with_validation(
            validation_policy(
                validator(
                    "non_empty",
                    lambda value: bool(value.strip()),
                    message="Value must not be empty.",
                )
            )
        )
        .run()
    )

    assert result.value == "valid response"
    assert calls == 3
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 3
    assert result.report.retry_count == 2
    assert result.report.attempts[0].metadata["validation_valid"] is False
    assert result.report.attempts[1].metadata["validation_valid"] is False
    assert result.report.attempts[2].metadata["validation_valid"] is True


async def test_async_operation_uses_fallback_after_validation_failure() -> None:
    async def primary() -> str:
        return " "

    async def fallback() -> str:
        return "fallback response"

    result = await (
        async_operation("provider_call", primary)
        .with_retry(
            retry_policy(
                max_attempts=1,
                retry_on=(ValidationFailedError,),
            )
        )
        .with_validation(
            validation_policy(
                validator(
                    "non_empty",
                    lambda value: bool(value.strip()),
                    message="Value must not be empty.",
                )
            )
        )
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .run()
    )

    assert result.value == "fallback response"
    assert result.report.status is ExecutionStatus.SUCCEEDED
    assert result.report.attempt_count == 2
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["validation_valid"] is True
    assert result.report.attempts[0].metadata["validation_valid"] is False
    assert result.report.attempts[1].metadata["fallback_used"] is True
    assert result.report.attempts[1].metadata["validation_valid"] is True


async def test_async_operation_raises_when_fallback_result_fails_validation() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def fallback() -> str:
        return " "

    with pytest.raises(OperationExecutionError) as exc_info:
        await (
            async_operation("provider_call", primary)
            .with_retry(
                retry_policy(
                    max_attempts=1,
                    retry_on=(TransientError,),
                )
            )
            .with_validation(
                validation_policy(
                    validator(
                        "non_empty",
                        lambda value: bool(value.strip()),
                        message="Value must not be empty.",
                    )
                )
            )
            .with_fallbacks(
                fallback_chain(
                    ("fallback", fallback),
                )
            )
            .run()
        )

    error = exc_info.value

    assert isinstance(error.cause, ValidationFailedError)
    assert error.report.status is ExecutionStatus.FAILED
    assert error.report.attempt_count == 2
    assert error.report.metadata["fallback_used"] is True
    assert error.report.metadata["fallback_failed"] is True
    assert error.report.metadata["validation_valid"] is False
    assert error.report.metadata["validation_validator_name"] == "non_empty"


async def test_async_operation_builder_preserves_validation_across_policy_changes() -> None:
    async def operation() -> str:
        return "valid response"

    configured_operation = (
        async_operation("provider_call", operation)
        .with_validation(
            validation_policy(
                validator(
                    "non_empty",
                    lambda value: bool(value.strip()),
                    message="Value must not be empty.",
                )
            )
        )
        .with_retry(retry_policy(max_attempts=2))
        .with_timeout(TimeoutPolicy(seconds=1))
    )

    result = await configured_operation.run()

    assert result.value == "valid response"
    assert result.report.metadata["validation_performed"] is True
    assert result.report.metadata["validation_valid"] is True


async def test_async_operation_emits_events_for_successful_operation() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def operation() -> str:
        return "ok"

    result = await async_operation("provider_call", operation).with_events(emitter).run()

    events = await sink.events()

    assert result.value == "ok"
    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_SUCCEEDED,
        EventType.OPERATION_SUCCEEDED,
    ]
    assert all(event.operation_name == "provider_call" for event in events)
    assert events[-1].payload["attempt_count"] == 1
    assert events[-1].payload["retry_count"] == 0


async def test_async_operation_emits_events_for_retry_flow() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1

        if calls < 2:
            raise TransientError("temporary failure")

        return "ok"

    result = await (
        async_operation("provider_call", operation)
        .with_events(emitter)
        .with_retry(retry_policy(max_attempts=2, retry_on=(TransientError,)))
        .run()
    )

    events = await sink.events()

    assert result.value == "ok"
    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_FAILED,
        EventType.RETRY_SCHEDULED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_SUCCEEDED,
        EventType.OPERATION_SUCCEEDED,
    ]
    assert events[2].payload["error_type"] == "TransientError"
    assert events[2].payload["retryable"] is True
    assert events[3].payload["next_attempt_number"] == 2
    assert events[-1].payload["retry_count"] == 1


async def test_async_operation_emits_events_for_failed_operation() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def operation() -> str:
        raise PermanentError("do not retry")

    with pytest.raises(OperationExecutionError):
        await async_operation("provider_call", operation).with_events(emitter).run()

    events = await sink.events()

    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_FAILED,
        EventType.OPERATION_FAILED,
    ]
    assert events[2].payload["error_type"] == "PermanentError"
    assert events[3].payload["attempt_count"] == 1
    assert events[3].payload["error_type"] == "PermanentError"


async def test_async_operation_emits_events_for_fallback_success() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def fallback() -> str:
        return "fallback-result"

    result = await (
        async_operation("provider_call", primary)
        .with_events(emitter)
        .with_retry(retry_policy(max_attempts=1, retry_on=(TransientError,)))
        .with_fallbacks(
            fallback_chain(
                ("fallback", fallback),
            )
        )
        .run()
    )

    events = await sink.events()

    assert result.value == "fallback-result"
    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_FAILED,
        EventType.FALLBACK_STARTED,
        EventType.FALLBACK_SUCCEEDED,
        EventType.ATTEMPT_SUCCEEDED,
        EventType.OPERATION_SUCCEEDED,
    ]
    assert events[4].payload["fallback_candidate_name"] == "fallback"
    assert events[-1].payload["fallback_used"] is True


async def test_async_operation_emits_events_for_validation_failure() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def operation() -> str:
        return " "

    with pytest.raises(OperationExecutionError):
        await (
            async_operation("provider_call", operation)
            .with_events(emitter)
            .with_validation(
                validation_policy(
                    validator(
                        "non_empty",
                        lambda value: bool(value.strip()),
                        message="Value must not be empty.",
                    )
                )
            )
            .run()
        )

    events = await sink.events()

    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.VALIDATION_FAILED,
        EventType.ATTEMPT_FAILED,
        EventType.OPERATION_FAILED,
    ]
    assert events[2].payload["validator_name"] == "non_empty"
    assert events[2].payload["reason"] == "Value must not be empty."
    assert events[3].payload["validation_valid"] is False


async def test_async_operation_emits_events_for_validation_success() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def operation() -> str:
        return "valid response"

    result = await (
        async_operation("provider_call", operation)
        .with_events(emitter)
        .with_validation(
            validation_policy(
                validator(
                    "non_empty",
                    lambda value: bool(value.strip()),
                    message="Value must not be empty.",
                )
            )
        )
        .run()
    )

    events = await sink.events()

    assert result.value == "valid response"
    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.VALIDATION_SUCCEEDED,
        EventType.ATTEMPT_SUCCEEDED,
        EventType.OPERATION_SUCCEEDED,
    ]
    assert events[2].payload["validator_name"] == "validation_policy"
    assert events[3].payload["validation_valid"] is True


async def test_async_operation_builder_preserves_event_emitter_across_policy_changes() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def operation() -> str:
        return "ok"

    configured_operation = (
        async_operation("provider_call", operation)
        .with_events(emitter)
        .with_retry(retry_policy(max_attempts=2))
        .with_timeout(TimeoutPolicy(seconds=1))
    )

    result = await configured_operation.run()

    events = await sink.events()

    assert result.value == "ok"
    assert events
    assert events[0].event_type is EventType.OPERATION_STARTED
    assert events[-1].event_type is EventType.OPERATION_SUCCEEDED
