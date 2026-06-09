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


async def test_resilient_decorator_accepts_simple_retries_option() -> None:
    calls = 0

    @resilient(
        name="provider_call",
        retries=2,
        retry_on=(TransientError,),
    )
    async def provider() -> str:
        nonlocal calls
        calls += 1

        if calls < 3:
            raise TransientError("temporary failure")

        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert calls == 3
    assert result.report.attempt_count == 3
    assert result.report.retry_count == 2


async def test_resilient_decorator_accepts_simple_timeout_option() -> None:
    @resilient(
        name="provider_call",
        timeout=10,
    )
    async def provider() -> str:
        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert result.report.succeeded is True


def test_resilient_decorator_rejects_retry_policy_and_simple_retries_together() -> None:
    with pytest.raises(ValueError, match="retry and retries cannot be used together"):
        resilient(
            retry=RetryPolicy(max_attempts=2),
            retries=2,
        )


def test_resilient_decorator_rejects_negative_retries() -> None:
    with pytest.raises(ValueError, match="retries must be greater than or equal to 0"):
        resilient(retries=-1)


def test_resilient_decorator_rejects_zero_or_negative_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be greater than 0"):
        resilient(timeout=0)

    with pytest.raises(ValueError, match="timeout must be greater than 0"):
        resilient(timeout=-1)


def test_resilient_decorator_rejects_invalid_timeout_type() -> None:
    with pytest.raises(TypeError, match="timeout must be a TimeoutPolicy"):
        resilient(timeout="10")  # type: ignore[arg-type]


async def test_resilient_decorator_allows_zero_retries_without_retry_policy() -> None:
    calls = 0

    @resilient(
        name="provider_call",
        retries=0,
    )
    async def provider() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert calls == 1
    assert result.report.attempt_count == 1
    assert result.report.retry_count == 0


async def test_resilient_decorator_accepts_simple_fallback_option() -> None:
    async def backup_provider(prompt: str) -> str:
        return f"backup response for: {prompt}"

    @resilient(
        name="provider_call",
        retries=0,
        fallback=backup_provider,
    )
    async def provider(prompt: str) -> str:
        raise PermanentError("primary unavailable")

    result = await provider("hello")

    assert result.value == "backup response for: hello"
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["fallback_candidate_name"] == "backup_provider"
    assert result.report.attempt_count == 2


def test_resilient_decorator_rejects_fallback_and_fallbacks_together() -> None:
    async def backup_provider() -> str:
        return "backup"

    with pytest.raises(ValueError, match="fallback and fallbacks cannot be used together"):
        resilient(
            fallback=backup_provider,
            fallbacks=fallback_chain(
                ("backup_provider", backup_provider),
            ),
        )


async def test_resilient_decorator_uses_fallback_function_name_as_candidate_name() -> None:
    async def gemini_provider(prompt: str) -> str:
        return f"gemini response for: {prompt}"

    @resilient(
        name="openai_provider",
        fallback=gemini_provider,
    )
    async def openai_provider(prompt: str) -> str:
        raise PermanentError("openai unavailable")

    result = await openai_provider("hello")

    assert result.value == "gemini response for: hello"
    assert result.report.metadata["fallback_candidate_name"] == "gemini_provider"
