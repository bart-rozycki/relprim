# Idempotency

Idempotency coordinates duplicate calls of the same logical operation: while
the process can observe an execution, calls with the same key join it, and
after it succeeds they replay its result.

It is useful when callers may retry requests, messages may be delivered more than once, or concurrent workers may attempt to perform the same operation.

Typical examples include:

* creating payments
* submitting orders
* sending notifications
* processing webhook deliveries
* starting background jobs
* writing to external APIs

## Not an exactly-once guarantee

RelPrim idempotency coordinates duplicate calls while the process can observe
their execution, but it does not guarantee exactly-once external side effects.

A side effect can still happen twice, for example when:

1. the owning call successfully performs an external side effect, such as
   charging a card;
2. the call is cancelled, or the process crashes or restarts, before RelPrim
   observes and caches the successful result;
3. another caller later executes the operation again with the same key.

No local, in-memory coordination around an external operation can rule this
out: the external system may complete an operation whose outcome never reaches
the caller. The same applies when an operation fails after part of its work
already took effect, because failures are not cached.

For operations with external side effects, such as payments, order creation,
provisioning, message publication or other external mutations, also use the
downstream provider's own idempotency mechanism where one exists, for example
by passing a provider-side idempotency key derived from the same logical
request:

```python
@resilient(
    idempotency_key=lambda request_id, amount_cents: f"create-payment:{request_id}",
)
async def create_payment(request_id: str, amount_cents: int) -> str:
    return await payment_gateway.create(
        amount_cents=amount_cents,
        idempotency_key=f"create-payment:{request_id}",
    )
```

Where the provider offers no such mechanism, use another durable coordination
mechanism, such as a unique constraint or an outbox table in your own
database. RelPrim then avoids redundant concurrent work and replays results,
and the provider or database guarantees that the side effect happens once.

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
    "idempotency_status": "replayed",
    "idempotency_cache_hit": True,
}
```

The idempotency key itself is never copied into execution reports, structured
events or exception messages. Keys often contain customer, order or payment
identifiers, and reports and events are designed to be logged and exported.
The key remains available in-process as `IdempotencyResult.key` when you use a
store directly, and as `IdempotencyReentryError.key`.

Joined and replayed results carry the execution report of the original
execution, and their `value` is the same object the original call returned. Do
not mutate shared values. Joined and replayed calls emit no structured events
because they did not execute the operation.

## Concurrent requests

Concurrent callers using the same key share one execution.

```python
first, second = await asyncio.gather(
    create_payment("request-123", 2499),
    create_payment("request-123", 2499),
)
```

One caller receives the `executed` status. The other receives `joined`.

The underlying operation runs once for both callers.

Keys are independent: an in-flight execution only affects callers using the
same key.

## Cancellation

* Cancelling a joined caller does not cancel the shared execution. The
  cancelled caller receives `asyncio.CancelledError`; everyone else still
  receives the result.
* Cancelling the owning caller cancels its execution and releases the key
  immediately. Nothing is cached. Callers that had joined the cancelled
  execution are not cancelled: one of them becomes the new owner and executes
  the operation, the others join it. If the cancelled execution had already
  performed an external side effect, the new owner performs it again; see
  [Not an exactly-once guarantee](#not-an-exactly-once-guarantee).
* A later call after any cancellation executes normally.

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

If an execution fails, every caller that joined it receives the same
exception, the key is released and a later call may try again.

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
async def create_payment(request_id: str, amount_cents: int) -> str: ...
```

The TTL is measured from the moment the execution succeeded. A result stored
with `ttl_seconds=3600` is replayed for calls made less than 3600 seconds
later; from exactly 3600 seconds on, the next caller executes the operation
again. TTLs must be finite and greater than zero.

The in-memory store removes expired entries lazily, during the next call,
`purge_expired()` or `entry_count()`. It runs no background cleanup.

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
async def create_payment(request_id: str, amount_cents: int) -> str: ...
```

Each decorated function gets its own store in that case.

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
async def create_payment(request_id: str, amount_cents: int) -> str: ...
```

Share one store between functions only when their keys are namespaced.

Shared or persistent stores can implement the `IdempotencyStore` protocol. An
implementation must execute an operation at most once at a time per key, let
concurrent callers share an in-flight outcome, replay successful results until
their TTL expires and never cache failures or cancellations.

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
    return await payment_gateway.create(
        request_id=request_id,
        amount_cents=amount_cents,
    )
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

Detection covers the owning task itself. If the operation starts a separate
task that uses the same key and then waits for it, that task joins the
execution it is waiting for, which never completes. Avoid reusing an
operation's own key inside it.

## Key validation

The key factory must return a non-blank string. Other return values raise
`TypeError` or `ValueError`, and exceptions raised by the key factory propagate
unchanged. These errors are raised before the operation runs and are not
wrapped in `OperationExecutionError`.
