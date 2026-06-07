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
    ExponentialBackoff,
    RetryPolicy,
    TimeoutPolicy,
    async_operation,
)


class TemporaryProviderError(Exception):
    pass


async def call_external_provider(prompt: str) -> str:
    # Replace this with an OpenAI, Gemini, HTTP, payment or any other external call.
    return f"Generated response for: {prompt}"


async def main() -> None:
    result = await (
        async_operation("generate_response", call_external_provider)
        .with_retry(
            RetryPolicy(
                max_attempts=3,
                retry_on=(TemporaryProviderError,),
                backoff=ExponentialBackoff(
                    base_delay_seconds=0.2,
                    max_delay_seconds=2.0,
                    jitter=True,
                ),
            )
        )
        .with_timeout(TimeoutPolicy(seconds=10))
        .run("Write a short product summary")
    )

    print(result.value)
    print(result.report.to_dict())


asyncio.run(main())
```

## Current Features

* Retry policies
* Async timeout enforcement
* Structured execution reports
* Operation results
* Typed execution errors

## Planned Features

* Circuit breakers
* Fallback chains
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
