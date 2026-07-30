from __future__ import annotations

import pytest

from relprim import (
    EventEmitter,
    EventType,
    IdempotencyStatus,
    InMemoryEventSink,
    InMemoryIdempotencyStore,
    OperationExecutionError,
    RateLimitPolicy,
    RetryPolicy,
    TimeoutPolicy,
    ValidationFailedError,
    fallback_chain,
    idempotency_policy,
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


async def test_resilient_decorator_accepts_simple_idempotency_key() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    def request_key(request_id: str) -> str:
        return request_id

    @resilient(
        name="create_payment",
        idempotency_key=request_key,
        idempotency_store=store,
        idempotency_ttl=60,
    )
    async def create_payment(request_id: str) -> str:
        nonlocal calls
        calls += 1
        return f"payment:{request_id}"

    first = await create_payment("request-123")
    second = await create_payment("request-123")

    assert first.value == "payment:request-123"
    assert second.value == "payment:request-123"
    assert calls == 1

    assert first.report.metadata["idempotency_status"] == IdempotencyStatus.EXECUTED.value
    assert second.report.metadata["idempotency_status"] == IdempotencyStatus.REPLAYED.value
    assert second.report.metadata["idempotency_cache_hit"] is True


async def test_resilient_decorator_creates_default_idempotency_store() -> None:
    calls = 0

    def request_key(request_id: str) -> str:
        return request_id

    @resilient(
        idempotency_key=request_key,
    )
    async def create_payment(request_id: str) -> str:
        nonlocal calls
        calls += 1
        return f"payment:{request_id}"

    first = await create_payment("request-123")
    second = await create_payment("request-123")

    assert first.value == "payment:request-123"
    assert second.value == "payment:request-123"
    assert calls == 1
    assert second.report.metadata["idempotency_status"] == "replayed"


async def test_resilient_decorator_executes_for_different_idempotency_keys() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    def request_key(request_id: str) -> str:
        return request_id

    @resilient(
        idempotency_key=request_key,
        idempotency_store=store,
    )
    async def create_payment(request_id: str) -> str:
        nonlocal calls
        calls += 1
        return f"payment:{request_id}"

    first = await create_payment("request-123")
    second = await create_payment("request-456")

    assert first.value == "payment:request-123"
    assert second.value == "payment:request-456"
    assert calls == 2


async def test_resilient_decorator_accepts_explicit_idempotency_policy() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    def request_key(request_id: str) -> str:
        return request_id

    policy = idempotency_policy(
        request_key,
        store=store,
        ttl_seconds=60,
    )

    @resilient(
        name="create_payment",
        idempotency=policy,
    )
    async def create_payment(request_id: str) -> str:
        nonlocal calls
        calls += 1
        return f"payment:{request_id}"

    first = await create_payment("request-123")
    second = await create_payment("request-123")

    assert first.value == "payment:request-123"
    assert second.value == "payment:request-123"
    assert calls == 1
    assert first.report.metadata["idempotency_status"] == "executed"
    assert second.report.metadata["idempotency_status"] == "replayed"


async def test_resilient_decorator_idempotency_wraps_retry_flow() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    def request_key(request_id: str) -> str:
        return request_id

    @resilient(
        retries=1,
        retry_on=(TransientError,),
        idempotency_key=request_key,
        idempotency_store=store,
    )
    async def create_payment(request_id: str) -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise TransientError("temporary failure")

        return f"payment:{request_id}"

    first = await create_payment("request-123")
    second = await create_payment("request-123")

    assert first.value == "payment:request-123"
    assert second.value == "payment:request-123"

    assert calls == 2
    assert first.report.attempt_count == 2
    assert first.report.retry_count == 1
    assert second.report.metadata["idempotency_status"] == "replayed"


def test_resilient_decorator_rejects_conflicting_idempotency_configuration() -> None:
    store = InMemoryIdempotencyStore()

    def request_key(request_id: str) -> str:
        return request_id

    policy = idempotency_policy(
        request_key,
        store=store,
    )

    def define_operation() -> None:
        @resilient(
            idempotency=policy,
            idempotency_key=request_key,
        )
        async def create_payment(request_id: str) -> str:
            return f"payment:{request_id}"

    with pytest.raises(
        ValueError,
        match="idempotency cannot be combined",
    ):
        define_operation()


def test_resilient_decorator_rejects_idempotency_store_without_key() -> None:
    store = InMemoryIdempotencyStore()

    def define_operation() -> None:
        @resilient(
            idempotency_store=store,
        )
        async def create_payment(request_id: str) -> str:
            return f"payment:{request_id}"

    with pytest.raises(
        ValueError,
        match="idempotency_store and idempotency_ttl require idempotency_key",
    ):
        define_operation()


def test_resilient_decorator_rejects_idempotency_ttl_without_key() -> None:
    def define_operation() -> None:
        @resilient(
            idempotency_ttl=60,
        )
        async def create_payment(request_id: str) -> str:
            return f"payment:{request_id}"

    with pytest.raises(
        ValueError,
        match="idempotency_store and idempotency_ttl require idempotency_key",
    ):
        define_operation()


def test_resilient_decorator_rejects_non_positive_idempotency_ttl() -> None:
    def request_key(request_id: str) -> str:
        return request_id

    def define_operation() -> None:
        @resilient(
            idempotency_key=request_key,
            idempotency_ttl=0,
        )
        async def create_payment(request_id: str) -> str:
            return f"payment:{request_id}"

    with pytest.raises(
        ValueError,
        match="ttl_seconds must be greater than 0",
    ):
        define_operation()


class ProviderRateLimitError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def provider_retry_after(
    exception: Exception,
) -> float | None:
    if isinstance(exception, ProviderRateLimitError):
        return exception.retry_after_seconds

    return None


async def test_resilient_decorator_accepts_rate_limit_policy() -> None:
    calls = 0

    policy = RateLimitPolicy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=provider_retry_after,
        max_wait_seconds=10,
    )

    @resilient(
        retry=RetryPolicy(
            max_attempts=2,
            async_sleeper=no_sleep,
        ),
        rate_limit=policy,
    )
    async def provider() -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise ProviderRateLimitError(
                "too many requests",
                retry_after_seconds=0,
            )

        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert calls == 2


def test_resilient_decorator_rejects_conflicting_rate_limit_configuration() -> None:
    policy = RateLimitPolicy(
        rate_limit_on=(ProviderRateLimitError,),
    )

    def define_operation() -> None:
        @resilient(
            rate_limit=policy,
            rate_limit_on=(ProviderRateLimitError,),
        )
        async def provider() -> str:
            return "ok"

    with pytest.raises(
        ValueError,
        match="rate_limit cannot be combined",
    ):
        define_operation()


def test_resilient_decorator_rejects_retry_after_without_rate_limit_on() -> None:
    def retry_after(exception: Exception) -> float | None:
        return 1

    def define_operation() -> None:
        @resilient(retry_after=retry_after)
        async def provider() -> str:
            return "ok"

    with pytest.raises(
        ValueError,
        match="require rate_limit_on",
    ):
        define_operation()


async def test_resilient_decorator_accepts_simple_rate_limit_options() -> None:
    calls = 0
    delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    @resilient(
        retry=RetryPolicy(
            max_attempts=2,
            async_sleeper=capture_sleep,
        ),
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=provider_retry_after,
        max_rate_limit_wait=10,
    )
    async def provider() -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise ProviderRateLimitError(
                "too many requests",
                retry_after_seconds=2,
            )

        return "ok"

    result = await provider()

    assert result.value == "ok"
    assert calls == 2
    assert delays == [2]
    assert result.report.metadata["rate_limit_encountered"] is True
    assert result.report.metadata["rate_limit_wait_seconds"] == 2
    assert result.report.metadata["rate_limit_wait_exceeded"] is False
