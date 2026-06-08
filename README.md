# RelPrim

[![CI](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml/badge.svg)](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/relprim.svg)](https://pypi.org/project/relprim/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/relprim/)
[![License](https://img.shields.io/pypi/l/relprim.svg)](https://github.com/bart-rozycki/relprim/blob/main/LICENSE)

Reliability primitives for external operations in Python.

RelPrim is a production resilience SDK that helps developers build reliable integrations with AI providers, APIs and external services.

## Status

🚧 Early development.

RelPrim is currently in active development and APIs may change before the first stable release.

## Installation

```bash
pip install relprim
```

## Why RelPrim?

Modern applications often depend on external systems:

* AI providers
* payment gateways
* third-party APIs
* internal services
* data platforms
* notification providers
* storage systems

These operations fail in predictable ways:

* timeouts
* transient errors
* unstable providers
* malformed responses
* overloaded downstream systems
* expensive or unreliable primary providers

RelPrim provides small, composable reliability primitives for handling these failures explicitly.

It is designed for engineers who want reliability behavior that is easy to read, test, observe and evolve.

## Quickstart

```python
import asyncio

from relprim import (
    CircuitBreaker,
    RetryPolicy,
    TimeoutPolicy,
    async_operation,
    fallback_chain,
    validation_policy,
    validator,
)


class TemporaryProviderError(Exception):
    pass


async def call_primary_provider(prompt: str) -> str:
    # Replace this with an OpenAI, Gemini, HTTP, payment or any other external call.
    raise TemporaryProviderError("primary provider temporarily unavailable")


async def call_fallback_provider(prompt: str) -> str:
    # Replace this with a secondary provider, backup API or local implementation.
    return f"Fallback response for: {prompt}"


async def main() -> None:
    circuit_breaker = CircuitBreaker(
        name="primary_provider",
        failure_threshold=3,
        recovery_timeout_seconds=30,
        record_failure_on=(TemporaryProviderError,),
    )

    result = await (
        async_operation("generate_response", call_primary_provider)
        .with_circuit_breaker(circuit_breaker)
        .with_retry(
            RetryPolicy(
                max_attempts=3,
                retry_on=(TemporaryProviderError,),
            )
        )
        .with_timeout(TimeoutPolicy(seconds=10))
        .with_validation(
            validation_policy(
                validator(
                    "non_empty_response",
                    lambda value: bool(value.strip()),
                    message="Response must not be empty.",
                )
            )
        )
        .with_fallbacks(
            fallback_chain(
                ("fallback_provider", call_fallback_provider),
            )
        )
        .run("Write a short product summary")
    )

    print(result.value)
    print(result.report.to_dict())


asyncio.run(main())
```

## What RelPrim provides

RelPrim focuses on reliability primitives for operations that cross process, network or provider boundaries.

Current primitives:

* Retry policies
* Exponential backoff with jitter
* Async timeout enforcement
* Async fallback chains
* Async circuit breakers
* Validation policies
* Callable validators
* Structured events
* Event emitters
* No-op event sink
* In-memory event sink
* Async resilient operation API
* Structured execution reports
* Operation results
* Typed execution errors

Planned primitives:

* Idempotency
* Rate limit handling
* JSON Schema validator adapter
* Pydantic validator adapter
* SQLite event store
* OpenTelemetry exporter

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

## Structured events

RelPrim can emit structured lifecycle events from async operations.

Events are opt-in. By default, operations do not emit events. When configured with an `EventEmitter`, an operation can emit events such as:

* `operation.started`
* `attempt.started`
* `attempt.failed`
* `retry.scheduled`
* `validation.failed`
* `fallback.started`
* `operation.succeeded`

```python
from relprim import EventEmitter, InMemoryEventSink, async_operation

event_sink = InMemoryEventSink()
event_emitter = EventEmitter(sinks=(event_sink,))

result = await (
    async_operation("generate_response", call_provider)
    .with_events(event_emitter)
    .run("Write a short product summary")
)

for event in await event_sink.events():
    print(event.to_dict())
```

Structured events are transport-agnostic. They can be sent to logs, in-memory sinks, SQLite stores, OpenTelemetry exporters or custom observability systems.

## Examples

Practical examples are available in the [`examples`](examples) directory:

* [`basic_resilience.py`](examples/basic_resilience.py) — retry, timeout and execution reports
* [`fallback_chain.py`](examples/fallback_chain.py) — primary provider failure with fallback execution
* [`circuit_breaker.py`](examples/circuit_breaker.py) — circuit breaker protection with fallback behavior
* [`validation.py`](examples/validation.py) — result validation with retry support
* [`structured_events.py`](examples/structured_events.py) — operation lifecycle events with retry and validation

Run an example:

```bash
python examples/basic_resilience.py
```

## Why operation and fallback names matter

RelPrim uses explicit operation and fallback names for observability.

```python
async_operation("generate_response", call_primary_provider)

fallback_chain(
    ("fallback_provider", call_fallback_provider),
)
```

These names appear in execution reports and structured events. They will also be used by persistent execution history and OpenTelemetry integration.

Example report metadata:

```python
{
    "fallback_used": True,
    "fallback_candidate_name": "fallback_provider",
    "fallback_candidate_index": 0,
    "circuit_breaker_open": False,
}
```

Explicit names make production debugging easier. They also avoid relying on unstable function names like `call`, `run`, `handler` or `invoke`.

## Roadmap

Near-term roadmap:

* SQLite execution/event store
* OpenTelemetry exporter
* JSON Schema validator adapter
* Pydantic validator adapter
* Idempotency helpers
* Rate limit handling

Later roadmap:

* Provider-specific examples
* HTTP integration examples
* AI provider integration examples
* CLI inspection tools

RelPrim will stay focused on reliability primitives. Provider adapters and workflow-style APIs may be added later only if they do not compromise the core model.

## What RelPrim is not

RelPrim is not:

* a workflow engine
* an agent framework
* a task queue
* a chatbot framework
* an AI provider wrapper
* a replacement for Temporal, Airflow, Celery, LangChain or LangGraph

It is a reliability SDK for external operations.

## Maintainer

Created and maintained by [Bart Rozycki](https://github.com/bart-rozycki).

## License

Apache License 2.0
