# Getting started

RelPrim provides reliability primitives for operations that cross process, network or provider boundaries.

The fastest way to use RelPrim is the `@resilient` decorator.

## Install

```bash
pip install relprim
```

## Basic decorator usage

```python
from relprim import resilient


@resilient(retries=3, timeout=10)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)


result = await call_provider("Write a short product summary")

print(result.value)
print(result.report.to_dict())
```

`retries=3` means three retry attempts after the initial call.

`timeout=10` means each attempt is protected by a 10 second async timeout.

The decorated function returns an `OperationResult[T]`.

```python
result.value
result.report
```

This keeps the business result and execution metadata explicit.

## Retry only selected exceptions

By default, simple retries use `Exception`.

For production code, prefer retrying specific transient errors.

```python
from relprim import resilient


class TemporaryProviderError(Exception):
    pass


@resilient(
    retries=3,
    retry_on=(TemporaryProviderError,),
    timeout=10,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

## Validate provider responses

External providers may return malformed, empty or unusable responses.

```python
from relprim import resilient, validation_policy, validator


@resilient(
    retries=2,
    timeout=10,
    validation=validation_policy(
        validator(
            "non_empty_response",
            lambda value: bool(value.strip()),
            message="Response must not be empty.",
        )
    ),
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

Validation failures are reported through the execution report.

They can also participate in retry behavior when configured explicitly.

```python
from relprim import ValidationFailedError, resilient


@resilient(
    retries=2,
    retry_on=(ValidationFailedError,),
    timeout=10,
    validation=validation_policy(...),
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

## Use advanced policies when you need control

The simple decorator options are designed for fast adoption.

For precise control, pass explicit policies.

```python
from relprim import RetryPolicy, TimeoutPolicy, resilient


@resilient(
    retry=RetryPolicy(max_attempts=3),
    timeout=TimeoutPolicy(seconds=10),
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

## When to use the builder API

Use the lower-level `async_operation(...)` builder when you want very explicit composition or when the operation is assembled dynamically.

```python
from relprim import RetryPolicy, TimeoutPolicy, async_operation


result = await (
    async_operation("generate_response", call_provider)
    .with_retry(RetryPolicy(max_attempts=3))
    .with_timeout(TimeoutPolicy(seconds=10))
    .run("Write a short product summary")
)
```

For most simple integrations, start with `@resilient(...)`.
