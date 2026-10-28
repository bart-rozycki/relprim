# RelPrim

[![CI](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml/badge.svg)](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/relprim.svg)](https://pypi.org/project/relprim/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/relprim/)
[![License](https://img.shields.io/pypi/l/relprim.svg)](https://github.com/bart-rozycki/relprim/blob/main/LICENSE)

Reliability primitives for operations that cross process, network or provider boundaries.

RelPrim helps you wrap external calls with retries, timeouts, fallbacks, validation, circuit breakers, idempotency, provider-aware rate-limit recovery, execution reports and structured events.

Documentation: [bartrozycki.com/relprim](https://bartrozycki.com/relprim) · [Examples](https://bartrozycki.com/relprim/examples)

## Install

```bash
pip install relprim
```

OpenTelemetry integration is available as an optional extra:

```bash
pip install "relprim[otel]"
```

JSON Schema and Pydantic validator adapters are also optional:

```bash
pip install "relprim[jsonschema]"
pip install "relprim[pydantic]"
```

## Wrap an external call in seconds

```python
from relprim import resilient


async def call_gemini(prompt: str) -> str:
    return await gemini_client.generate(prompt)


@resilient(retries=3, timeout=10, fallback=call_gemini)
async def call_openai(prompt: str) -> str:
    return await openai_client.generate(prompt)


result = await call_openai("Write a short product summary")

print(result.value)
print(result.report.to_dict())
```

The decorated function returns an `OperationResult[T]`, not a raw value. This keeps the business result and the execution report explicit.

`retries=3` allows three retries after the initial attempt, `timeout=10` limits
each attempt to ten seconds, and the fallback runs once the retries are
exhausted. If everything fails, `OperationExecutionError` is raised with the
execution report attached.

RelPrim is fully typed: decorated functions keep their parameters, and type
checkers see `OperationResult[str]` as the result above.

## Why RelPrim?

Most external calls start simple:

```python
response = await openai.chat.completions.create(...)
```

But production systems need to answer harder questions:

* What if the provider times out?
* What if the response is temporarily unavailable?
* What if the provider returns an invalid response?
* What if the primary provider is down?
* What if you need a fallback provider?
* What if you need to debug what happened after the fact?

RelPrim gives you two levels of adoption.

Beginner-friendly decorator API:

```python
@resilient(retries=3, timeout=10)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

Advanced composition API:

```python
result = await (
    async_operation("generate_response", call_provider)
    .with_retry(RetryPolicy(max_attempts=3))
    .with_timeout(TimeoutPolicy(seconds=10))
    .with_validation(validation_policy(...))
    .with_fallbacks(fallback_chain(("backup_provider", call_backup)))
    .run(prompt)
)
```

## What RelPrim provides

Current primitives:

* Resilient decorator API
* Retry policies
* Exponential backoff with jitter
* Provider-aware rate-limit recovery
* Provider retry-after delay handling
* Async timeout enforcement
* Async fallback chains
* Async circuit breakers
* Validation policies
* Callable validators
* JSON Schema validator adapter
* Pydantic validator adapter
* Structured events
* Event emitters
* No-op event sink
* In-memory event sink
* SQLite event store and persistent event history
* OpenTelemetry event sink
* Async operation builder API
* Structured execution reports
* Operation results
* Typed execution errors
* Idempotency policies
* Concurrent execution joining
* Successful result replay
* In-memory idempotency store

New features are driven by user feedback.

## Validate responses with JSON Schema or Pydantic

Plain callable validators are dependency-free. When responses already have a
schema or model, optional adapters plug into the same validation policy.

JSON Schema:

```python
from relprim import resilient, validation_policy
from relprim.jsonschema import json_schema_validator


schema = {
    "type": "object",
    "required": ["id", "name"],
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
}


@resilient(retries=2, validation=validation_policy(json_schema_validator(schema)))
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

Pydantic:

```python
from pydantic import BaseModel

from relprim import resilient, validation_policy
from relprim.pydantic import pydantic_validator


class User(BaseModel):
    id: int
    name: str


@resilient(retries=2, validation=validation_policy(pydantic_validator(User)))
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

Invalid responses fail with `ValidationFailedError` and a concise reason such as
`'name' is a required property (path: $)`. Results are validated, never
converted.

See the [JSON Schema validation guide](https://github.com/bart-rozycki/relprim/blob/main/docs/json-schema-validation.md) and the
[Pydantic validation guide](https://github.com/bart-rozycki/relprim/blob/main/docs/pydantic-validation.md) for details.

## Deduplicate repeated calls

RelPrim can deduplicate repeated or concurrent calls using an idempotency key.

```python
from relprim import resilient


@resilient(
    retries=2,
    timeout=10,
    idempotency_key=lambda request_id, amount: f"create-payment:{request_id}",
    idempotency_ttl=3600,
)
async def create_payment(
    request_id: str,
    amount: int,
) -> str:
    return await payment_gateway.create(request_id, amount)
```

The first call executes the operation. Concurrent callers join the same execution, and later calls replay the successful result.

This is not an exactly-once guarantee for external side effects: if a call is cancelled or the process crashes after the side effect but before the result is cached, a later call executes again. For payments and other external mutations, also use the provider's own idempotency key.

The default store is in-memory and single-process. See the [idempotency guide](https://github.com/bart-rozycki/relprim/blob/main/docs/idempotency.md) for concurrency semantics, key design and store limitations.

## Respect provider rate limits

RelPrim can use retry delays supplied by an external provider and avoid waiting
longer than the current operation allows.

Provider SDKs expose retry information differently, so a small extractor
translates the provider exception into a delay expressed in seconds:

```python
def provider_retry_after(
    exception: Exception,
) -> float | None:
    if isinstance(exception, ProviderRateLimitError):
        return exception.retry_after_seconds

    return None
```

Pass the extractor to `@resilient(...)`:

```python
@resilient(
    retries=3,
    timeout=10,
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_rate_limit_wait=30,
    fallback=call_backup_provider,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

RelPrim uses the provider delay when available and falls back to the normal
retry backoff otherwise. When the selected delay exceeds
`max_rate_limit_wait`, the operation continues into fallback or final failure.

See the [rate-limit handling guide](https://github.com/bart-rozycki/relprim/blob/main/docs/rate-limits.md) for delay selection,
report metadata, structured events and limitations.

## Persist structured event history

RelPrim can persist structured lifecycle events in a local SQLite database.

```python
from relprim import EventEmitter, SQLiteEventStore, resilient


event_store = SQLiteEventStore("relprim-events.db")
event_emitter = EventEmitter(sinks=(event_store,))


@resilient(
    retries=3,
    timeout=10,
    events=event_emitter,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

Stored events can be queried later:

```python
history = await event_store.history(
    operation_name="call_provider",
    limit=100,
)
```

See the [SQLite event store guide](https://github.com/bart-rozycki/relprim/blob/main/docs/sqlite-event-store.md) for filtering,
pagination, retention and storage limitations.

## Export reliability events to OpenTelemetry

RelPrim can export its structured lifecycle events to the currently active
OpenTelemetry span.

```python
from relprim import EventEmitter, resilient
from relprim.opentelemetry import OpenTelemetryEventSink


event_emitter = EventEmitter(sinks=(OpenTelemetryEventSink(),))


@resilient(
    retries=3,
    timeout=10,
    events=event_emitter,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

The application remains responsible for configuring its OpenTelemetry SDK,
tracer provider and exporter.

See the [OpenTelemetry integration guide](https://github.com/bart-rozycki/relprim/blob/main/docs/opentelemetry.md) for setup,
exported attributes and limitations.

## Examples

Practical examples are available in the [`examples`](https://github.com/bart-rozycki/relprim/tree/main/examples) directory, and as walkthroughs organized by problem in the [documentation](https://bartrozycki.com/relprim/examples):

* [`decorator_usage.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/decorator_usage.py) — beginner-friendly decorator API
* [`basic_resilience.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/basic_resilience.py) — retry, timeout and execution reports
* [`fallback_chain.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/fallback_chain.py) — primary provider failure with fallback execution
* [`circuit_breaker.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/circuit_breaker.py) — circuit breaker protection with fallback behavior
* [`validation.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/validation.py) — result validation with retry support
* [`json_schema_validation.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/json_schema_validation.py) — JSON Schema response validation with retry recovery
* [`pydantic_validation.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/pydantic_validation.py) — Pydantic response validation with fallback recovery
* [`structured_events.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/structured_events.py) — operation lifecycle events with retry and validation
* [`idempotency.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/idempotency.py) — duplicate call coordination and result replay
* [`rate_limit.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/rate_limit.py) — provider retry-after handling and maximum wait enforcement
* [`sqlite_event_store.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/sqlite_event_store.py) — durable structured events and basic history queries
* [`opentelemetry_integration.py`](https://github.com/bart-rozycki/relprim/blob/main/examples/opentelemetry_integration.py) — structured reliability events exported to an active OpenTelemetry span

Every example runs without credentials or network access. If you run examples from a cloned repository, install RelPrim in editable mode first:

```bash
python -m pip install -e ".[dev]"
python examples/decorator_usage.py
```

Or run a single example without installing the package:

```bash
PYTHONPATH=src python examples/decorator_usage.py
```

## Documentation

The full documentation, including scenario-based [examples](https://bartrozycki.com/relprim/examples), is at [bartrozycki.com/relprim](https://bartrozycki.com/relprim).

Source-level guides in this repository:

* [Getting started](https://github.com/bart-rozycki/relprim/blob/main/docs/getting-started.md)
* [Advanced usage](https://github.com/bart-rozycki/relprim/blob/main/docs/advanced-usage.md)
* [Idempotency](https://github.com/bart-rozycki/relprim/blob/main/docs/idempotency.md)
* [Rate-limit handling](https://github.com/bart-rozycki/relprim/blob/main/docs/rate-limits.md)
* [SQLite event store](https://github.com/bart-rozycki/relprim/blob/main/docs/sqlite-event-store.md)
* [OpenTelemetry integration](https://github.com/bart-rozycki/relprim/blob/main/docs/opentelemetry.md)
* [JSON Schema validation](https://github.com/bart-rozycki/relprim/blob/main/docs/json-schema-validation.md)
* [Pydantic validation](https://github.com/bart-rozycki/relprim/blob/main/docs/pydantic-validation.md)

Reference:

* [API reference](https://github.com/bart-rozycki/relprim/blob/main/docs/api-reference.md)
* [Structured events and metadata](https://github.com/bart-rozycki/relprim/blob/main/docs/events-and-metadata.md)
* [Stability and versioning](https://github.com/bart-rozycki/relprim/blob/main/docs/stability.md)
* [Migrating from 0.11 to 1.0](https://github.com/bart-rozycki/relprim/blob/main/docs/migration-to-1.0.md)

## Stability

RelPrim follows semantic versioning. The public API, documented behavior,
event identifiers and documented metadata keys only change incompatibly in a
new major release. Underscore-prefixed names and undocumented internals are not
part of that promise. See [stability and versioning](https://github.com/bart-rozycki/relprim/blob/main/docs/stability.md).

RelPrim supports Python 3.11–3.14.

## Design principles

RelPrim is intentionally small and explicit.

Core principles:

* Reliability behavior should be visible in code.
* Failure modes should be explicit.
* Defaults should be safe for production use.
* Primitives should be composable, not magical.
* Observability should be built into the execution model.
* Async execution should respect cancellation and timeout semantics.
* The library should not hide side effects behind fake safety guarantees.
* External integrations should be wrapped, not replaced.

RelPrim does not try to become a workflow engine. It provides the reliability layer that can be used inside your application, worker, service or orchestration system.

## What RelPrim is not

RelPrim is not:

* an AI provider SDK
* an HTTP client
* a workflow engine
* a task queue
* an observability backend
* a replacement for provider-native SDKs
* a replacement for Temporal, Celery or OpenTelemetry

It is a reliability layer for external operations.

## Maintainer

Created and maintained by [Bart Rozycki](https://github.com/bart-rozycki).

## License

Apache License 2.0
