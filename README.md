# RelPrim

Reliability primitives for external operations in Python.

RelPrim is a production resilience SDK that helps developers build reliable integrations with AI providers, APIs and external services.

## Status

🚧 Early development.

RelPrim is currently in active development and APIs may change before the first stable release.

## Installation

```bash
pip install relprim
```

## Quickstart

```python
import asyncio

from relprim import (
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
    result = await (
        async_operation("generate_response", call_primary_provider)
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
* Async resilient operation API
* Structured execution reports
* Operation results
* Typed execution errors

Planned primitives:

* Circuit breakers
* Validation
* Idempotency
* Rate limit handling
* Structured events
* SQLite event store
* OpenTelemetry exporter

## What RelPrim is not

RelPrim is not a workflow engine, agent framework, task queue, chatbot framework or AI provider wrapper.

It is a reliability SDK for external operations.

## License

Apache License 2.0
