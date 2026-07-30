# Rate-limit handling

RelPrim can recover from external operations rejected because of provider rate
limits.

The rate-limit policy identifies provider-specific exceptions, extracts an
optional retry delay and integrates that information with the existing retry
and fallback flow.

This feature handles recovery after a provider has rejected an operation. It is
not a request throttler, token bucket or distributed quota manager.

## Basic usage

Different provider SDKs expose rate-limit information differently.

For example, a provider exception may contain the recommended delay as an
attribute:

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
```

RelPrim uses an extractor function to translate the provider-specific exception
into a delay expressed in seconds:

```python
def provider_retry_after(
    exception: Exception,
) -> float | None:
    if isinstance(exception, ProviderRateLimitError):
        return exception.retry_after_seconds

    return None
```

The extractor returns:

- a positive number when the provider supplied a retry delay;
- `0` when an immediate retry is allowed;
- `None` when the provider did not supply a usable delay.

Pass the exception type and extractor to `@resilient(...)`:

```python
from relprim import resilient


@resilient(
    retries=3,
    timeout=10,
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_rate_limit_wait=30,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

`retries=3` allows up to three retries after the initial attempt.

Rate-limit handling does not create additional attempts by itself. A retry
policy must be configured for a rate-limited operation to be retried.

## Retry delay selection

When RelPrim detects a configured rate-limit exception, it selects the delay for
the next attempt using the following rules:

1. Use the provider-supplied retry delay when one is available.
2. Otherwise use the backoff configured by `RetryPolicy`.
3. Do not retry when the selected delay exceeds the configured maximum wait.

`RetryPolicy` controls how many attempts are allowed.

`RateLimitPolicy` controls which exceptions represent rate limits, how the
provider delay is extracted and how long the operation may wait.

A configured rate-limit exception does not also need to appear in
`RetryPolicy.retry_on`. Rate-limit exceptions are evaluated by the rate-limit
policy before the normal retry exception filter.

## Missing provider hints

Some providers report a rate limit without telling the caller when to retry.

When the extractor returns `None`, RelPrim uses the delay calculated by the
normal retry backoff:

```python
from relprim import ExponentialBackoff, RetryPolicy, resilient


@resilient(
    retry=RetryPolicy(
        max_attempts=4,
        backoff=ExponentialBackoff(
            base_delay_seconds=0.5,
            max_delay_seconds=10,
        ),
    ),
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_rate_limit_wait=30,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

The retry backoff remains responsible for jitter and backoff progression.

## Maximum acceptable wait

Provider delays may be technically valid but too long for the current
operation.

Use `max_rate_limit_wait` to define the longest acceptable delay:

```python
@resilient(
    retries=3,
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_rate_limit_wait=5,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

When the selected delay exceeds five seconds, RelPrim does not sleep and does
not issue another primary attempt.

The operation continues into its normal fallback or final failure path.

## Using a fallback

A fallback can be used when waiting for the primary provider would take too
long:

```python
@resilient(
    retries=3,
    timeout=10,
    rate_limit_on=(ProviderRateLimitError,),
    retry_after=provider_retry_after,
    max_rate_limit_wait=5,
    fallback=call_backup_provider,
)
async def call_primary_provider(prompt: str) -> str:
    return await primary_provider.generate(prompt)
```

A short provider delay may therefore lead to another primary attempt, while a
long delay may lead directly to the backup provider.

## Advanced policy API

Use `RateLimitPolicy` directly when configuring the builder API or when keeping
policy construction separate from the decorator:

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
    async_operation("call_provider", call_provider)
    .with_retry(
        RetryPolicy(
            max_attempts=4,
        )
    )
    .with_rate_limit(rate_limits)
    .run("Write a short product summary")
)
```

The equivalent advanced decorator configuration is:

```python
from relprim import RateLimitPolicy, RetryPolicy, resilient


@resilient(
    retry=RetryPolicy(max_attempts=4),
    rate_limit=RateLimitPolicy(
        rate_limit_on=(ProviderRateLimitError,),
        retry_after=provider_retry_after,
        max_wait_seconds=30,
    ),
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

Do not combine `rate_limit=RateLimitPolicy(...)` with the simple
`rate_limit_on`, `retry_after` or `max_rate_limit_wait` arguments.

## Execution reports

A rate-limited attempt contains metadata describing the decision:

```python
{
    "rate_limited": True,
    "rate_limit_retry_after_seconds": 2.0,
    "rate_limit_delay_seconds": 2.0,
    "rate_limit_delay_source": "provider",
    "rate_limit_wait_exceeded": False,
}
```

When the provider does not supply a delay,
`rate_limit_delay_source` is `"backoff"`.

The final execution report includes an operation-level summary:

```python
{
    "rate_limit_encountered": True,
    "rate_limit_wait_seconds": 2.0,
    "rate_limit_wait_exceeded": False,
}
```

`rate_limit_wait_seconds` contains the total time spent waiting because of
rate-limit recovery during the operation.

## Structured events

When structured events are enabled, rate-limit handling may emit:

- `rate_limit.detected`;
- `retry.scheduled`;
- `rate_limit.wait_exceeded`.

The `retry.scheduled` event includes:

- the selected delay;
- whether the failure was rate-limited;
- the provider retry-after value, when available;
- the delay source: `provider` or `backoff`.

## Extractor failures

The retry-after extractor must return an `int`, `float` or `None`.

Returned numbers must be finite and greater than or equal to zero.

When the extractor raises an exception or returns an invalid value, RelPrim
raises `RetryAfterExtractionError`. The operation is not retried using an
untrusted delay.

## Scope and limitations

Rate-limit handling does not prevent an application from exceeding a provider
quota.

It does not provide:

- proactive request throttling;
- token-bucket or leaky-bucket enforcement;
- distributed quota coordination;
- per-tenant quota accounting;
- provider-specific exception adapters;
- replacement for provider-native rate-limit support.

Those responsibilities remain with the application, gateway or provider SDK.
