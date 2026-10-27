# API reference

This page lists the supported public API of RelPrim 1.0. It is an index, not a
copy of the docstrings: see the linked guides for behavior in depth.

The compatibility promise for everything listed here is described in the
[stability policy](stability.md).

## What is public

The public API consists of:

* every name in `relprim.__all__`, imported from the top-level `relprim`
  package;
* every name in `__all__` of the optional integration modules
  `relprim.opentelemetry`, `relprim.jsonschema` and `relprim.pydantic`;
* the documented constructor parameters, attributes, methods and properties of
  those names;
* the documented runtime behavior, including the
  [structured-event and metadata contract](events-and-metadata.md).

Not public:

* anything whose name starts with an underscore, including the dataclass
  fields of `AsyncOperation` and modules such as `relprim._checks`;
* the location of core names in submodules (`relprim.retry`,
  `relprim.operation`, ...). Import core names from `relprim`;
* module-level names that are not in `__all__`;
* exception message text, `repr()` output and the exact wording of
  validation reasons;
* the `dev` extra.

## Decorator

| Name | Purpose |
| --- | --- |
| `resilient(...)` | Decorate an `async def` so it returns `OperationResult[R]`. |
| `ResilientDecorator[RF]` | Protocol returned by `resilient(...)`, for type annotations. |

`resilient` options and their policy counterparts are mutually exclusive:

| Simple option | Advanced option | Builder method |
| --- | --- | --- |
| `retries`, `retry_on` | `retry=RetryPolicy(...)` | `with_retry` |
| `timeout=<seconds>` | `timeout=TimeoutPolicy(...)` | `with_timeout` |
| `fallback=<callable>` | `fallbacks=fallback_chain(...)` | `with_fallbacks` |
| — | `circuit_breaker=CircuitBreaker(...)` | `with_circuit_breaker` |
| — | `validation=validation_policy(...)` | `with_validation` |
| — | `events=EventEmitter(...)` | `with_events` |
| `idempotency_key`, `idempotency_store`, `idempotency_ttl` | `idempotency=idempotency_policy(...)` | `with_idempotency` |
| `rate_limit_on`, `retry_after`, `max_rate_limit_wait` | `rate_limit=RateLimitPolicy(...)` | `with_rate_limit` |

* `retries=N` allows N retries after the initial attempt, which equals
  `RetryPolicy(max_attempts=N + 1)`. `retries=0` disables retrying.
* `retry_on` defaults to `(Exception,)` and requires `retries`.
* `name` defaults to the decorated function's `__name__`.
* Invalid or conflicting options raise `ValueError` or `TypeError` when
  `resilient(...)` is called.

Typing: the decorated callable keeps the original parameters and returns
`Coroutine[Any, Any, OperationResult[R]]`, or `OperationResult[R | RF]` when a
fallback returning `RF` is configured. The decorator does not statically check
that `validation`, `fallback` or `idempotency_key` match the decorated
function; the builder API does.

## Builder

| Name | Purpose |
| --- | --- |
| `async_operation(name, operation)` | Create an `AsyncOperation[P, R]`. |
| `AsyncOperation` | Immutable operation configuration. Construct it with `async_operation`. |
| `AsyncOperation.with_*(policy)` | Return a new operation with one policy replaced. |
| `AsyncOperation.run(*args, **kwargs)` | Execute and return `OperationResult[R]`, or raise `OperationExecutionError`. |

Builder methods never mutate the receiver. Calling the same method twice keeps
the last policy. The order of builder calls does not change behavior: the
execution order is always the one below.

Execution order of one `run()`:

1. Idempotency (wraps everything below).
2. For each primary attempt, up to `RetryPolicy.max_attempts`:
   circuit breaker → timeout → operation → validation.
3. On a retryable failure with attempts remaining: rate-limit or backoff
   delay, then the next attempt.
4. After the final primary failure, if a fallback chain is configured and its
   `FallbackPolicy` matches the failure: the fallback chain, validating each
   candidate's result. Fallback candidates run without the timeout policy and
   outside the circuit breaker.

## Retry

| Name | Notes |
| --- | --- |
| `RetryPolicy(max_attempts=3, backoff=ExponentialBackoff(), retry_on=(Exception,), sleeper=time.sleep, async_sleeper=asyncio.sleep)` | `max_attempts` counts the initial attempt. `retry_on` must contain `Exception` subclasses. |
| `RetryPolicy.run(...)`, `RetryPolicy.run_async(...)` | Standalone use; raise `RetryError` after the final attempt. |
| `ExponentialBackoff(base_delay_seconds=0.1, max_delay_seconds=10.0, multiplier=2.0, jitter=True)` | `delay_for_retry(n)` is 1-based. With jitter, a uniform delay in `[0, computed]`. |

## Timeout

| Name | Notes |
| --- | --- |
| `TimeoutPolicy(seconds)` | Async only. Raises `OperationTimeoutError` only when its own deadline expires. |

## Fallback

| Name | Notes |
| --- | --- |
| `fallback_chain(("name", operation), ..., policy=None)` | Create a `FallbackChain`. |
| `FallbackChain`, `FallbackCandidate` | Ordered candidates; `FallbackChain.from_operations(...)` is equivalent to `fallback_chain(...)`. |
| `FallbackPolicy(fallback_on=(Exception,))` | Decides which failures trigger a fallback — of the primary operation and of each candidate. |
| `FallbackResult` | Returned by `FallbackChain.run()` used standalone. |

## Circuit breaker

| Name | Notes |
| --- | --- |
| `CircuitBreaker(name="default", failure_threshold=5, recovery_timeout_seconds=30.0, record_failure_on=(Exception,), clock=time.monotonic)` | Shared stateful object; compare by identity. |
| `CircuitBreaker.run_async`, `snapshot()`, `reset()` | |
| `CircuitBreakerState` | `closed`, `open`, `half_open`. |
| `CircuitBreakerSnapshot` | Immutable state snapshot. |

Inside an operation every primary attempt, including validation and timeout
failures, is a breaker call. Cancelled calls are neither successes nor
failures.

## Validation

| Name | Notes |
| --- | --- |
| `Validator[T]` | Protocol: read-only `name` and `validate(value) -> ValidationResult`. Contravariant. |
| `validator(name, predicate, *, message)` / `CallableValidator` | Dependency-free validator. |
| `validation_policy(*validators)` / `ValidationPolicy` | Fail-fast, in order. Contravariant. |
| `ValidationPolicy.validate(value)` | First failing result, or the last validator's success result. |
| `ValidationPolicy.validate_or_raise(value)` | Raises `ValidationFailedError`; returns the success result. |
| `ValidationResult` | `valid`, `validator_name`, `reason`. |
| `relprim.jsonschema.json_schema_validator` / `JSONSchemaValidator` | See [JSON Schema validation](json-schema-validation.md). |
| `relprim.pydantic.pydantic_validator` / `PydanticValidator` | See [Pydantic validation](pydantic-validation.md). |

## Idempotency

| Name | Notes |
| --- | --- |
| `idempotency_policy(key_factory, *, store=None, ttl_seconds=3600.0)` / `IdempotencyPolicy` | |
| `IdempotencyStore` | Protocol for custom stores. |
| `InMemoryIdempotencyStore(clock=time.monotonic)` | Single process, single event loop. `purge_expired()`, `entry_count()`. |
| `IdempotencyResult`, `IdempotencyStatus` | `executed`, `joined`, `replayed`. |

See the [idempotency guide](idempotency.md).

## Rate limits

| Name | Notes |
| --- | --- |
| `RateLimitPolicy(rate_limit_on, retry_after=None, max_wait_seconds=60.0)` / `rate_limit_policy(...)` | |
| `RetryAfterExtractor` | `Callable[[Exception], float \| None]`. |
| `RateLimitDecision` | Result of `RateLimitPolicy.evaluate()`. |

See [rate-limit handling](rate-limits.md).

## Events

| Name | Notes |
| --- | --- |
| `EventType` | Stable event identifiers. |
| `StructuredEvent` | Immutable; timezone-aware timestamp; JSON-scalar payload. |
| `EventEmitter(sinks=(NoopEventSink(),), timestamp_factory=...)` | Awaits sinks in order. |
| `EventSink` | Protocol: `async emit(event)`. |
| `NoopEventSink`, `InMemoryEventSink` | |
| `SQLiteEventStore`, `StoredEvent` | See [SQLite event store](sqlite-event-store.md). |
| `relprim.opentelemetry.OpenTelemetryEventSink` | See [OpenTelemetry integration](opentelemetry.md). |

See [structured events and metadata](events-and-metadata.md) for every event
and key.

## Reports and results

| Name | Notes |
| --- | --- |
| `OperationResult[T]` | `value`, `report`. Returned for successful executions only. Covariant. |
| `ExecutionReport` | `status`, `attempts`, `attempt_count`, `retry_count`, `retried`, `last_attempt`, `last_error`, `metadata`, `to_dict()`. |
| `ExecutionAttempt` | `attempt_number`, `status`, `started_at`, `duration_seconds`, `error`, `metadata`, `fallback`. |
| `ExecutionError` | Serializable error summary: `type`, `message`, `module`, `retryable`. |
| `ExecutionStatus`, `AttemptStatus` | `succeeded`, `failed`, `timed_out`. |

`attempt_count` includes the fallback attempt; `retry_count` counts primary
attempts after the first one only. `ExecutionError.retryable` records whether
the failure was classified as retryable, even on the final attempt.

## Exceptions

```text
Exception
└── RelPrimError
    ├── OperationExecutionError      .report, .cause
    ├── RetryError                   .attempts, .cause
    ├── OperationTimeoutError        .timeout_seconds, .cause
    ├── FallbackChainError           .failures, .cause
    ├── CircuitBreakerOpenError      .breaker_name, .state, .retry_after_seconds
    ├── ValidationFailedError        .validator_name, .reason
    ├── RetryAfterExtractionError    .rate_limit_error, .cause
    ├── IdempotencyReentryError      .key
    └── SQLiteEventStoreSchemaError
```

What `AsyncOperation.run()` and decorated functions raise:

| Situation | Exception |
| --- | --- |
| The operation failed after retries and fallbacks | `OperationExecutionError`; `.cause` is the final failure (for example `OperationTimeoutError`, `ValidationFailedError`, `CircuitBreakerOpenError`, `FallbackChainError` or the operation's own exception) |
| The surrounding task was cancelled | `asyncio.CancelledError`, never wrapped |
| An event sink raised | The sink's exception, unchanged |
| The idempotency key factory raised or returned a non-string or blank key | The original exception, `TypeError` or `ValueError` |
| The same idempotency key was reused recursively | `IdempotencyReentryError` |

Configuration mistakes raise `ValueError` or `TypeError` when a policy is
constructed or `resilient(...)` is called. Optional integrations deliberately
let third-party configuration errors through unchanged, for example
`jsonschema.exceptions.SchemaError` for an invalid schema. `SQLiteEventStore`
propagates `sqlite3` errors unchanged.

`RetryError` is only raised by the standalone `RetryPolicy.run*` methods.
