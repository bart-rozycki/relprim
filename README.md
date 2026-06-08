# RelPrim

[![CI](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml/badge.svg)](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/relprim.svg)](https://pypi.org/project/relprim/)
[![Python versions](https://img.shields.io/pypi/pyversions/relprim.svg)](https://pypi.org/project/relprim/)
[![License](https://img.shields.io/pypi/l/relprim.svg)](https://github.com/bart-rozycki/relprim/blob/main/LICENSE)

Reliability primitives for external operations in Python.

RelPrim is a production resilience SDK that helps developers build reliable integrations with AI providers, APIs and external services.

## Status

🚧 Early development.

RelPrim is currently in active development and APIs may change before the first stable release.

## Installation

```bash
pip install relprim
````

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
* Async resilient operation API
* Structured execution reports
* Operation results
* Typed execution errors

Planned primitives:

* Validation
* Idempotency
* Rate limit handling
* Structured events
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

## Examples

Practical examples are available in the [`examples`](examples) directory:

* [`basic_resilience.py`](examples/basic_resilience.py) — retry, timeout and execution reports
* [`fallback_chain.py`](examples/fallback_chain.py) — primary provider failure with fallback execution
* [`circuit_breaker.py`](examples/circuit_breaker.py) — circuit breaker protection with fallback behavior

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

These names appear in execution reports and will later be used by structured events, persistent execution history and OpenTelemetry integration.

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

* Validation primitives
* Operation-level validation support
* Structured event sink
* In-memory event sink for tests and demos
* SQLite execution/event store
* OpenTelemetry exporter

Later roadmap:

* Idempotency keys
* Rate limit handling
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

## License

Apache License 2.0
