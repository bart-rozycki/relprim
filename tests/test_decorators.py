from __future__ import annotations

import pytest

from relprim import (
    EventEmitter,
    EventType,
    InMemoryEventSink,
    OperationExecutionError,
    RetryPolicy,
    TimeoutPolicy,
    ValidationFailedError,
    fallback_chain,
    resilient,
    validation_policy,
    validator,
)


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


async def no_sleep(delay: float) -> None:
    return None


async def test_resilient_decorator_returns_operation_result() -> None:
    @resilient(name="provider_call")
    async def provider(prompt: str) -> str:
        return f"response for: {prompt}"

    result = await provider("hello")

    assert result.value == "response for: hello"
    assert result.report.operation_name == "provider_call"
    assert result.report.succeeded is True
    assert result.report.attempt_count == 1


async def test_resilient_decorator_uses_function_name_by_default() -> None:
    @resilient()
    async def provider_call() -> str:
        return "ok"

    result = await provider_call()

    assert result.value == "ok"
    assert result.report.operation_name == "provider_call"


async def test_resilient_decorator_preserves_function_metadata() -> None:
    @resilient(name="provider_call")
    async def provider() -> str:
        """Provider docstring."""
        return "ok"

    assert provider.__name__ == "provider"
    assert provider.__doc__ == "Provider docstring."


def test_resilient_decorator_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        resilient(name=" ")


async def test_resilient_decorator_does_not_call_function_at_decoration_time() -> None:
    calls = 0

    @resilient(name="provider_call")
    async def provider() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert calls == 0

    result = await provider()

    assert result.value == "ok"
    assert calls == 1


async def test_resilient_decorator_applies_retry_policy() -> None:
    calls = 0

    @resilient(
        name="provider_call",
        retry=RetryPolicy(
            max_attempts=2,
            retry_on=(TransientError,),
            async_sleeper=no_sleep,
        ),
    )
    async def provider() -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise TransientError("temporary failure")

        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert calls == 2
    assert result.report.attempt_count == 2
    assert result.report.retry_count == 1


async def test_resilient_decorator_applies_validation_policy() -> None:
    @resilient(
        name="provider_call",
        validation=validation_policy(
            validator(
                "non_empty",
                lambda value: bool(value.strip()),
                message="Value must not be empty.",
            )
        ),
    )
    async def provider() -> str:
        return "valid response"

    result = await provider()

    assert result.value == "valid response"
    assert result.report.metadata["validation_performed"] is True
    assert result.report.metadata["validation_valid"] is True


async def test_resilient_decorator_reports_validation_failure() -> None:
    @resilient(
        name="provider_call",
        validation=validation_policy(
            validator(
                "non_empty",
                lambda value: bool(value.strip()),
                message="Value must not be empty.",
            )
        ),
    )
    async def provider() -> str:
        return " "

    with pytest.raises(OperationExecutionError) as exc_info:
        await provider()

    error = exc_info.value

    assert isinstance(error.cause, ValidationFailedError)
    assert error.report.metadata["validation_performed"] is True
    assert error.report.metadata["validation_valid"] is False
    assert error.report.metadata["validation_validator_name"] == "non_empty"


async def test_resilient_decorator_applies_fallback_chain() -> None:
    async def fallback(prompt: str) -> str:
        return f"fallback for: {prompt}"

    @resilient(
        name="provider_call",
        retry=RetryPolicy(
            max_attempts=1,
            retry_on=(TransientError,),
            async_sleeper=no_sleep,
        ),
        fallbacks=fallback_chain(
            ("fallback_provider", fallback),
        ),
    )
    async def provider(prompt: str) -> str:
        raise TransientError("primary unavailable")

    result = await provider("hello")

    assert result.value == "fallback for: hello"
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_candidate_name"] == "fallback_provider"
    assert result.report.attempt_count == 2


async def test_resilient_decorator_applies_timeout_policy() -> None:
    @resilient(
        name="provider_call",
        timeout=TimeoutPolicy(seconds=1),
    )
    async def provider() -> str:
        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert result.report.succeeded is True


async def test_resilient_decorator_applies_event_emitter() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    @resilient(
        name="provider_call",
        events=emitter,
    )
    async def provider() -> str:
        return "ok"

    result = await provider()
    events = await sink.events()

    assert result.value == "ok"
    assert [event.event_type for event in events] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_SUCCEEDED,
        EventType.OPERATION_SUCCEEDED,
    ]


async def test_resilient_decorator_supports_retry_validation_and_events_together() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))
    calls = 0

    @resilient(
        name="provider_call",
        events=emitter,
        retry=RetryPolicy(
            max_attempts=2,
            retry_on=(ValidationFailedError,),
            async_sleeper=no_sleep,
        ),
        validation=validation_policy(
            validator(
                "non_empty",
                lambda value: bool(value.strip()),
                message="Value must not be empty.",
            )
        ),
    )
    async def provider() -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            return " "

        return "valid response"

    result = await provider()
    events = await sink.events()

    assert result.value == "valid response"
    assert calls == 2
    assert result.report.attempt_count == 2
    assert EventType.VALIDATION_FAILED in [event.event_type for event in events]
    assert EventType.RETRY_SCHEDULED in [event.event_type for event in events]
    assert EventType.VALIDATION_SUCCEEDED in [event.event_type for event in events]
