# Advanced usage

RelPrim exposes low-level primitives and a composable async operation builder for advanced reliability workflows.

Use this API when you need explicit composition of retry, timeout, fallback, circuit breaker, validation, idempotency, rate-limit handling and structured events.

## Builder API

```python
from relprim import (
    RetryPolicy,
    TimeoutPolicy,
    async_operation,
)


result = await (
    async_operation("generate_response", call_provider)
    .with_retry(RetryPolicy(max_attempts=3))
    .with_timeout(TimeoutPolicy(seconds=10))
    .run("Write a short product summary")
)
```

The result contains both the business value and the execution report.

```python
print(result.value)
print(result.report.to_dict())
```

## Fallback chains

Fallback chains let you try backup providers after the primary operation fails.

```python
from relprim import fallback_chain


result = await (
    async_operation("generate_response", call_primary_provider)
    .with_retry(RetryPolicy(max_attempts=2))
    .with_fallbacks(
        fallback_chain(
            ("backup_provider", call_backup_provider),
        )
    )
    .run("Write a short product summary")
)
```

Fallback candidate names appear in reports and structured events.

## Circuit breakers

Circuit breakers protect overloaded or unhealthy downstream systems.

```python
from relprim import CircuitBreaker


circuit_breaker = CircuitBreaker(
    name="primary_provider",
    failure_threshold=3,
    recovery_timeout_seconds=30,
)

result = await (
    async_operation("generate_response", call_primary_provider)
    .with_circuit_breaker(circuit_breaker)
    .with_retry(RetryPolicy(max_attempts=3))
    .run("Write a short product summary")
)
```

## Rate-limit handling

Rate-limit policies recover from provider rejections using retry delays supplied
by the provider.

Define the provider exception and an extractor that returns the suggested delay
in seconds:

```python
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
```

Create a `RateLimitPolicy` and add it to the operation:

```python
from relprim import (
    RateLimitPolicy,
    RetryPolicy,
    async_operation,
)


rate_limits = RateLimitPolicy(
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_wait_seconds=30,
)

result = await (
    async_operation("generate_response", call_provider)
    .with_retry(
        RetryPolicy(
            max_attempts=4,
        )
    )
    .with_rate_limit(rate_limits)
    .run("Write a short product summary")
)
```

`RetryPolicy` controls the total number of attempts.

`RateLimitPolicy` identifies rate-limit exceptions, extracts the provider delay
and defines the maximum acceptable wait.

When the provider does not supply a delay, RelPrim uses the retry policy's
backoff. When the selected delay exceeds `max_wait_seconds`, RelPrim skips the
retry and continues into fallback or final failure.

The rate-limit exception does not need to appear in `RetryPolicy.retry_on`.
Configured rate-limit exceptions are handled by `RateLimitPolicy` before the
normal retry exception filter.

See the [rate-limit handling guide](rate-limits.md) for decorator configuration,
fallback behavior, metadata, structured events and limitations.

## Validation

Validation policies can reject invalid operation results before they are accepted as successful.

```python
from relprim import validation_policy, validator


response_validation = validation_policy(
    validator(
        "non_empty_response",
        lambda value: bool(value.strip()),
        message="Response must not be empty.",
    )
)

result = await (
    async_operation("generate_response", call_provider)
    .with_validation(response_validation)
    .run("Write a short product summary")
)
```

Validation failures are captured in execution reports.

## Structured events

RelPrim can emit structured lifecycle events from async operations.

Events are opt-in. By default, operations do not emit events.

```python
from relprim import EventEmitter, InMemoryEventSink


event_sink = InMemoryEventSink()
event_emitter = EventEmitter(sinks=(event_sink,))

result = await (
    async_operation("generate_response", call_provider)
    .with_events(event_emitter)
    .with_retry(RetryPolicy(max_attempts=3))
    .run("Write a short product summary")
)

for event in await event_sink.events():
    print(event.to_dict())
```

Events are transport-agnostic. They can be sent to logs, in-memory sinks, SQLite stores, OpenTelemetry exporters or custom observability systems.

## Idempotency

Idempotency can coordinate the entire operation lifecycle under one logical key.

```python
from relprim import (
    InMemoryIdempotencyStore,
    RetryPolicy,
    async_operation,
    idempotency_policy,
)


store = InMemoryIdempotencyStore()

payment_idempotency = idempotency_policy(
    lambda request_id, amount: f"create-payment:{request_id}",
    store=store,
    ttl_seconds=3600,
)

result = await (
    async_operation("create_payment", create_payment)
    .with_retry(RetryPolicy(max_attempts=3))
    .with_idempotency(payment_idempotency)
    .run("request-123", 2499)
)
```
Idempotency wraps the full retry, timeout, validation and fallback flow.

The in-memory store is limited to one process. See the [idempotency guide](idempotency.md) for key design, concurrency semantics and storage limitations.


## Operation names

RelPrim uses explicit operation names for observability.

```python
async_operation("generate_response", call_provider)
```

Operation names appear in execution reports and structured events.

Avoid unstable or generic names like `call`, `run`, `handler` or `invoke`.

## Decorator vs builder API

Use the decorator API when you want simple adoption.

```python
@resilient(retries=3, timeout=10)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

Use the builder API when you want explicit composition.

```python
result = await (
    async_operation("generate_response", call_provider)
    .with_retry(...)
    .with_rate_limit(...)
    .with_idempotency(...)
    .with_timeout(...)
    .with_validation(...)
    .with_fallbacks(...)
    .with_events(...)
    .run(prompt)
)
```

Both APIs use the same underlying primitives.
