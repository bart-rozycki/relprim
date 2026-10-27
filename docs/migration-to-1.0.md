# Migrating from 0.11 to 1.0

RelPrim 1.0 freezes the public API. Before that, the 0.11 API was hardened:
several pre-1.0 inconsistencies were fixed. This page lists every user-visible
change. Most applications need no code changes.

## Typing

### `@resilient(...)` now preserves signatures

**0.11:** most configurations inferred
`(*Never, **Never) -> Awaitable[OperationResult[Never]]` and were rejected by
strict mypy. In addition, the package had no `py.typed` marker, so type
checkers treated all of RelPrim as `Any`.

**1.0:** the decorated function keeps its parameters and returns
`Coroutine[Any, Any, OperationResult[R]]`, or `OperationResult[R | RF]` when a
fallback returning `RF` is configured. The package ships `py.typed`.

**Migration:** remove casts and `# type: ignore` comments added as
workarounds. Strict mypy reports the ones that are no longer needed.

### Variance

`ValidationPolicy` and `CallableValidator` are contravariant;
`OperationResult`, `FallbackChain`, `FallbackCandidate` and `FallbackResult`
are covariant. `validation_policy(json_schema_validator(...))` is now
`ValidationPolicy[object]` instead of `ValidationPolicy[Any]`.

**Migration:** none at runtime.

## Configuration

### `retry_on` requires `retries`

**0.11:** `@resilient(retry_on=...)` without `retries`, or together with
`retry=RetryPolicy(...)`, was silently ignored.

**1.0:** both raise `ValueError`.

**Migration:** add `retries=N`, or set `RetryPolicy(retry_on=...)`.

### Exception filters only accept `Exception` subclasses

**0.11:** `RetryPolicy.retry_on`, `FallbackPolicy.fallback_on` and
`CircuitBreaker.record_failure_on` accepted `BaseException` subclasses.
`RetryPolicy.run_async` would then retry `asyncio.CancelledError` and turn it
into `RetryError`.

**1.0:** these tuples must contain `Exception` subclasses; anything else raises
`TypeError`. Cancellation is never retried, never falls back and is never
recorded by a breaker.

**Migration:** remove `BaseException`, `asyncio.CancelledError`,
`KeyboardInterrupt` or `SystemExit` from these tuples.

### Stricter value validation

Durations (`TimeoutPolicy.seconds`, `ttl_seconds`, `max_wait_seconds`,
backoff delays, `recovery_timeout_seconds`, `busy_timeout_seconds` and the
matching decorator options) must be finite; `nan` or `inf` raise
`ValueError`. `ExecutionAttempt.duration_seconds` and
`ExecutionReport.duration_seconds` must be finite, non-negative `int` or
`float` values; booleans and other types raise `TypeError`. Booleans are
rejected for `max_attempts`, `failure_threshold` and `retries`. Event payloads
and report metadata must contain JSON scalars.

Report and event timestamps and the `SQLiteEventStore.delete_before()` cutoff
must be timezone-aware: a `tzinfo` whose `utcoffset()` returns `None` counts as
naive and raises `ValueError`. 0.11 accepted such a cutoff and interpreted it
as local time.

**Migration:** pass finite numbers. A `nan` TTL previously cached results
forever.

## Behavior

### Successful validation names a real validator

**0.11:** a successful `ValidationPolicy` reported
`validator_name="validation_policy"`.

**1.0:** it reports the last validator, all of which ran and passed. This
affects `ValidationResult.validator_name`, the `validation_validator_name`
metadata and the `validation.succeeded` event's `validator_name`.

**Migration:** update consumers matching on `"validation_policy"`.

`ValidationPolicy.validate_or_raise()` now returns the successful
`ValidationResult` instead of `None`.

### Fallback candidates are validated individually

**0.11:** validation ran once on the result of the whole chain. An invalid
result from the first candidate failed the operation without trying the next
candidate, and the failure's cause was `ValidationFailedError`.

**1.0:** each candidate's result is validated. An invalid result is a
candidate failure, so the chain's `FallbackPolicy` decides whether the next
candidate runs. When every candidate fails, the cause is `FallbackChainError`,
whose `failures` include the `ValidationFailedError`s. Validation events for
fallback candidates include `fallback_candidate_name`.

**Migration:** code checking `isinstance(error.cause, ValidationFailedError)`
after a fallback failure should inspect `error.cause.failures`.

### `FallbackPolicy` also filters the primary failure

**0.11:** inside an operation, the fallback chain ran after any primary
failure; `fallback_on` was only applied between candidates.

**1.0:** the primary failure must match `fallback_on` too. With the default
`(Exception,)` nothing changes.

**Migration:** if you narrowed `fallback_on`, make sure it includes every
primary failure that should fall back, such as `CircuitBreakerOpenError` or
`OperationTimeoutError`.

### `retry_count` excludes the fallback attempt

**0.11:** `ExecutionReport.retry_count` was `attempt_count - 1`, so a fallback
counted as a retry.

**1.0:** `retry_count` counts primary attempts after the first one.
`attempt_count` still includes the fallback attempt, which is marked with the
new `ExecutionAttempt.fallback` field (also in `to_dict()`). The
`operation.succeeded` and `operation.failed` events use the same value.

**Migration:** none, unless dashboards relied on the old count.

### `TimeoutPolicy` only reports its own timeout

**0.11:** a `TimeoutError` raised by the operation itself, such as a client
socket timeout, was converted into `OperationTimeoutError`.

**1.0:** only expiry of the policy's deadline raises `OperationTimeoutError`;
other `TimeoutError`s propagate unchanged and are reported as normal failures.

**Migration:** to retry client timeouts, add `TimeoutError` to `retry_on`.

### Rate-limit metadata

* `rate_limit_delay_seconds` and `rate_limit_delay_source` are `None` for
  failures that were not rate-limited. 0.11 filled them with the normal
  backoff delay.
* When the selected delay exceeds the maximum wait, they hold the rejected
  delay. 0.11 reported `None`, and the `rate_limit.wait_exceeded` event's
  `delay_seconds` was always `None`.
* On the final attempt no delay is selected, so `rate_limit_wait_exceeded`
  stays `False` and no `rate_limit.wait_exceeded` event is emitted.

### Idempotency

* The raw key is no longer copied into report metadata
  (`idempotency_key` was removed) or into the `IdempotencyReentryError`
  message. `IdempotencyResult.key` and `IdempotencyReentryError.key` are
  unchanged.
* If the owning caller is cancelled, joined callers no longer receive
  `asyncio.CancelledError`. One of them executes the operation instead.

**Migration:** if you need the key in logs, log it yourself.

### Circuit breaker cancellation

**0.11:** a cancelled half-open probe kept the probe slot occupied, so the
breaker rejected every later call.

**1.0:** a cancelled call releases the probe slot and is neither a success nor
a failure.

### Identity semantics for stateful objects

`CircuitBreaker`, `InMemoryIdempotencyStore`, `InMemoryEventSink` and
`SQLiteEventStore` compare by identity and are hashable. In 0.11 they compared
by field values and were unhashable.

`CircuitBreakerOpenError.state` is a `CircuitBreakerState`. It is a string
enum, so comparisons with `"open"` keep working.

### Optional integrations

Importing `relprim.opentelemetry`, `relprim.jsonschema` or `relprim.pydantic`
without the dependency raises `ModuleNotFoundError` with the `pip install`
command for the matching extra.

## Removed

| Removed | Reason | Migration |
| --- | --- | --- |
| `RetryAttempt` | Never produced by RelPrim | Remove the import |
| `ExecutionStatus.CANCELLED`, `AttemptStatus.CANCELLED` | Never produced: cancellation propagates `asyncio.CancelledError` and creates no report | Handle `asyncio.CancelledError` |
| Report metadata `idempotency_key` | May contain sensitive identifiers | Log the key yourself if needed |

## Added

* `ResilientDecorator`, the protocol returned by `resilient(...)`.
* `ExecutionAttempt.fallback`.
* Python 3.14 support.
