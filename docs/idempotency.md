# Idempotency

Idempotency prevents duplicate executions of the same logical operation.

It is useful when callers may retry requests, messages may be delivered more than once, or concurrent workers may attempt to perform the same operation.

Typical examples include:

* creating payments
* submitting orders
* sending notifications
* processing webhook deliveries
* starting background jobs
* writing to external APIs

## Basic decorator usage

```python
from relprim import resilient


def payment_key(
    request_id: str,
    amount_cents: int,
) -> str:
    return f"create-payment:{request_id}"


@resilient(
    retries=2,
    timeout=10,
    idempotency_key=payment_key,
    idempotency_ttl=3600,
)
async def create_payment(
    request_id: str,
    amount_cents: int,
) -> str:
    return await payment_gateway.create(
        request_id=request_id,
        amount_cents=amount_cents,
    )
```

The idempotency key factory receives the same positional and keyword arguments as the decorated operation.

The first call executes the operation:

```python
first = await create_payment("request-123", 2499)
```

A subsequent call using the same key replays the successful result:

```python
second = await create_payment("request-123", 2499)
```

## Execution statuses

Every idempotent result has one of three statuses.

| Status     | Meaning                                              |
| ---------- | ---------------------------------------------------- |
| `executed` | This caller owned and executed the operation.        |
| `joined`   | This caller joined an operation already in progress. |
| `replayed` | This caller received a previously completed result.  |

The status is included in the execution report:

```python
result.report.metadata["idempotency_status"]
```

Additional metadata includes:

```python
{
    "idempotency_enabled": True,
    "idempotency_key": "create-payment:request-123",
    "idempotency_status": "replayed",
    "idempotency_cache_hit": True,
}
```

## Concurrent requests

Concurrent callers using the same key share one execution.

```python
first, second = await asyncio.gather(
    create_payment("request-123", 2499),
    create_payment("request-123", 2499),
)
```

One caller receives the `executed` status. The other receives `joined`.

The underlying operation runs only once.

Cancelling a waiting caller does not cancel the shared execution.

## Retries, validation and fallbacks

Idempotency wraps the entire RelPrim execution lifecycle.

This includes:

* retries
* timeouts
* validation
* circuit breaker checks
* fallback execution
* execution reports
* structured events emitted by the underlying execution

A replayed call does not start a second retry or fallback flow. It receives the result of the original completed execution.

## Failed executions

Failed operations are not cached.

If an execution fails, the key is released and a later call may try again.

```python
try:
    await create_payment("request-123", 2499)
except OperationExecutionError:
    # A later call using the same key may execute again.
    ...
```

## Expiration

Successful results are retained until the configured TTL expires.

```python
@resilient(
    idempotency_key=payment_key,
    idempotency_ttl=3600,
)
async def create_payment(...) -> str:
    ...
```

After expiration, the next caller executes the operation again.

The TTL should match the retry and duplicate-delivery window of the surrounding system.

## Designing idempotency keys

A good idempotency key must identify one logical operation.

Prefer namespaced keys:

```python
def payment_key(request_id: str, amount_cents: int) -> str:
    return f"create-payment:{request_id}"
```

Avoid unscoped values:

```python
def unsafe_key(request_id: str, amount_cents: int) -> str:
    return request_id
```

Namespacing is especially important when multiple operations share one store.

An idempotency key must not be reused for a different logical request or different payload. RelPrim currently assumes that callers preserve this invariant.

Do not include unstable values such as:

* timestamps generated during the call
* random UUIDs generated inside the key factory
* process-specific identifiers
* mutable object representations

If the key changes between duplicate calls, those calls cannot be deduplicated.

## In-memory store limitations

The simple decorator configuration creates an `InMemoryIdempotencyStore` by default.

```python
@resilient(
    idempotency_key=payment_key,
)
async def create_payment(...) -> str:
    ...
```

The in-memory store coordinates calls only within one Python process and event loop.

It is not:

* a distributed lock
* shared between application processes
* shared between containers
* durable across restarts
* suitable as the only idempotency guarantee for a multi-instance service

You may provide an explicit store:

```python
from relprim import InMemoryIdempotencyStore, resilient


store = InMemoryIdempotencyStore()


@resilient(
    idempotency_key=payment_key,
    idempotency_store=store,
    idempotency_ttl=3600,
)
async def create_payment(...) -> str:
    ...
```

A future persistent store can implement the same `IdempotencyStore` protocol.

## Advanced policy API

Use an explicit policy when you want reusable configuration.

```python
from relprim import (
    InMemoryIdempotencyStore,
    idempotency_policy,
    resilient,
)


store = InMemoryIdempotencyStore()

payment_idempotency = idempotency_policy(
    payment_key,
    store=store,
    ttl_seconds=3600,
)


@resilient(
    idempotency=payment_idempotency,
)
async def create_payment(
    request_id: str,
    amount_cents: int,
) -> str:
    ...
```

The builder API supports the same policy:

```python
result = await (
    async_operation("create_payment", create_payment_raw)
    .with_retry(RetryPolicy(max_attempts=3))
    .with_idempotency(payment_idempotency)
    .run("request-123", 2499)
)
```

## Recursive reuse

RelPrim detects recursive reuse of the same idempotency key by the task that owns the execution.

Instead of deadlocking, it raises `IdempotencyReentryError`.

This commonly indicates that the same policy was accidentally applied at multiple nested layers of one operation.
