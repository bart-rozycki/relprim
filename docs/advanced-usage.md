# Advanced usage

RelPrim exposes low-level primitives and a composable async operation builder for advanced reliability workflows.

Use this API when you need explicit composition of retry, timeout, fallback, circuit breaker, validation and structured events.

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
    .with_timeout(...)
    .with_fallbacks(...)
    .with_events(...)
    .run(prompt)
)
```

Both APIs use the same underlying primitives.
