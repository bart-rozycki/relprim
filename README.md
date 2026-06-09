# RelPrim

[![CI](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml/badge.svg)](https://github.com/bart-rozycki/relprim/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/relprim.svg)](https://pypi.org/project/relprim/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/relprim/)
[![License](https://img.shields.io/pypi/l/relprim.svg)](https://github.com/bart-rozycki/relprim/blob/main/LICENSE)

Reliability primitives for operations that cross process, network or provider boundaries.

RelPrim helps you wrap external calls with retries, timeouts, fallbacks, validation, circuit breakers, execution reports and structured events.

## Install

```bash
pip install relprim
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
* Async timeout enforcement
* Async fallback chains
* Async circuit breakers
* Validation policies
* Callable validators
* Structured events
* Event emitters
* No-op event sink
* In-memory event sink
* Async operation builder API
* Structured execution reports
* Operation results
* Typed execution errors

Planned primitives:

* SQLite event store
* OpenTelemetry exporter
* Idempotency helpers
* Rate limit handling
* JSON Schema validator adapter
* Pydantic validator adapter

## Examples

Practical examples are available in the [`examples`](examples) directory:

* [`decorator_usage.py`](examples/decorator_usage.py) — beginner-friendly decorator API
* [`basic_resilience.py`](examples/basic_resilience.py) — retry, timeout and execution reports
* [`fallback_chain.py`](examples/fallback_chain.py) — primary provider failure with fallback execution
* [`circuit_breaker.py`](examples/circuit_breaker.py) — circuit breaker protection with fallback behavior
* [`validation.py`](examples/validation.py) — result validation with retry support
* [`structured_events.py`](examples/structured_events.py) — operation lifecycle events with retry and validation

If you run examples from a cloned repository, install RelPrim in editable mode first:

```bash
python -m pip install -e ".[dev]"
python examples/decorator_usage.py
```

Or run a single example without installing the package:

```bash
PYTHONPATH=src python examples/decorator_usage.py
```

## Documentation

* [Getting started](docs/getting-started.md)
* [Advanced usage](docs/advanced-usage.md)

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
