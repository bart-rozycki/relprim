# Structured events and metadata

RelPrim describes every execution twice:

* the **execution report** attached to `OperationResult.report` and
  `OperationExecutionError.report`;
* optional **structured events** delivered to the sinks of an
  `EventEmitter` while the operation runs.

Event identifiers and the keys documented on this page are
compatibility-sensitive: see the [stability policy](stability.md). The
contract is pinned by `tests/test_event_contract.py`.

## General rules

* Event payloads and report metadata are read-only mappings of JSON scalars:
  `str`, `int`, `float`, `bool` or `None`. Floats are always finite. RelPrim
  validates this when an event, attempt or report is created.
* Timestamps (`StructuredEvent.timestamp`, `ExecutionReport.started_at`,
  `ExecutionAttempt.started_at`) are timezone-aware. RelPrim produces UTC.
* Durations are finite, non-negative floats in seconds and their keys end
  with `_seconds`.
* Attempt numbers are 1-based. The fallback attempt is numbered after the last
  primary attempt.
* Status-like values are lower-case strings: `delay_source` is `"provider"` or
  `"backoff"`; `idempotency_status` is `"executed"`, `"joined"` or
  `"replayed"`; `state` is a `CircuitBreakerState` value.
* Each mapping is copied on creation; results never share metadata objects.
* New event types and new keys may be added in minor releases. Consumers must
  ignore unknown event types and keys.
* Idempotency keys are never copied into events or report metadata.

## Event delivery

`EventEmitter.emit()` awaits every sink in order, inline, inside the operation.
There are no background tasks and no buffering.

If a sink raises, the exception propagates to the caller unchanged and later
sinks do not receive that event. Inside an operation, the operation stops at
that point:

* the sink error is not retried, does not trigger fallbacks and is not wrapped
  in `OperationExecutionError`;
* side effects of the operation may already have happened;
* with idempotency, the execution counts as failed: nothing is cached and a
  later call executes again.

This is deliberate: RelPrim never hides storage or observability failures. If
an application prefers to isolate them, it can wrap its sink:

```python
import logging

from relprim import EventSink, StructuredEvent

logger = logging.getLogger(__name__)


class IsolatedSink:
    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    async def emit(self, event: StructuredEvent) -> None:
        try:
            await self._sink.emit(event)
        except Exception:
            logger.exception("RelPrim event delivery failed")
```

Cancellation while a sink is awaited propagates as `asyncio.CancelledError`.

Replayed and joined idempotent calls emit no events: they did not execute the
operation. The events of the original execution describe it.

## Event types

Event names are `EventType` values. The OpenTelemetry sink prefixes them with
`relprim.`.

| Event | Emitted when |
| --- | --- |
| `operation.started` | An execution starts |
| `attempt.started` | A primary attempt starts |
| `validation.succeeded` | A result passed validation |
| `validation.failed` | A result failed validation |
| `circuit_breaker.rejected` | The breaker rejected an attempt |
| `rate_limit.detected` | A failure matched the rate-limit policy |
| `rate_limit.wait_exceeded` | The selected delay exceeded `max_wait_seconds` |
| `attempt.failed` | An attempt failed (also emitted for a failed fallback) |
| `retry.scheduled` | A retry delay is about to be awaited |
| `attempt.succeeded` | An attempt succeeded (also emitted for a successful fallback) |
| `fallback.started` | The fallback chain starts |
| `fallback.succeeded` | A fallback candidate produced a valid result |
| `fallback.failed` | The fallback chain failed |
| `operation.succeeded` | The execution succeeded |
| `operation.failed` | The execution failed |

## Event payloads

"Always" keys are present on every event of that type. "Sometimes" keys are
present only on the paths described.

### `operation.started`

Always: `retry_enabled`, `timeout_enabled`, `fallback_enabled`,
`circuit_breaker_enabled`, `validation_enabled`, `rate_limit_enabled`.

### `attempt.started`

Always: `attempt_number`, `max_attempts`.

### `attempt.succeeded` and `attempt.failed`

Always: `attempt_number`, `duration_seconds` and the
[attempt metadata](#attempt-metadata) of that attempt. `attempt.failed` also
always has `error_type` (the exception class name) and `retryable`.

### `retry.scheduled`

Always: `attempt_number`, `next_attempt_number`, `delay_seconds`,
`delay_source`, `rate_limited`, `retry_after_seconds`.

### `validation.succeeded` and `validation.failed`

Always: `attempt_number`, `validator_name`; `validation.failed` also has
`reason`.

Sometimes: `fallback_used` (`True`) and `fallback_candidate_name` when a
fallback candidate's result was validated.

### `circuit_breaker.rejected`

Always: `attempt_number`, `breaker_name`, `state`, `retry_after_seconds`
(`None` while a half-open probe is in flight).

### `rate_limit.detected`

Always: `attempt_number`, `error_type`, `retry_after_seconds` (the provider
hint or `None`), `max_wait_seconds`, `max_wait_exceeded`.

### `rate_limit.wait_exceeded`

Always: `attempt_number`, `retry_after_seconds`, `delay_seconds` (the rejected
delay), `max_wait_seconds`.

### `fallback.started`

Always: `attempt_number`, `after_primary_attempts`.

### `fallback.succeeded` and `fallback.failed`

Always: `attempt_number`, `duration_seconds` and the fallback
[attempt metadata](#attempt-metadata). `fallback.failed` also has `error_type`.

### `operation.succeeded` and `operation.failed`

Always: `duration_seconds`, `attempt_count`, `retry_count` and the
[report metadata](#report-metadata). `operation.failed` also has `error_type`.

## Report metadata

`ExecutionReport.metadata` always contains:

| Key | Meaning |
| --- | --- |
| `fallback_used` | A fallback attempt ran |
| `circuit_breaker_open` | The final attempt was rejected by the circuit breaker |
| `validation_performed`, `validation_valid`, `validation_validator_name`, `validation_reason` | Outcome of the final validation |
| `rate_limit_encountered` | Any attempt was rate-limited |
| `rate_limit_wait_seconds` | Total time waited because of rate limits |
| `rate_limit_wait_exceeded` | A rate-limit delay was rejected because it exceeded the maximum wait |

Depending on the path it also contains the keys of the final attempt
(fallback or rate-limit keys below) and, with idempotency:

| Key | Meaning |
| --- | --- |
| `idempotency_enabled` | Always `True` when present |
| `idempotency_status` | `executed`, `joined` or `replayed` |
| `idempotency_cache_hit` | `True` for `joined` and `replayed` |

Joined and replayed results carry the report of the original execution, with
these idempotency keys describing how the caller obtained it.

## Attempt metadata

Every `ExecutionAttempt.metadata` contains `fallback_used`,
`circuit_breaker_open` and the four `validation_*` keys.

Failed primary attempts also contain:

| Key | Meaning |
| --- | --- |
| `rate_limited` | The failure matched the rate-limit policy |
| `rate_limit_retry_after_seconds` | Provider hint, or `None` |
| `rate_limit_delay_seconds` | Selected rate-limit delay, or `None` when no delay was selected (not rate-limited, or no attempt left) |
| `rate_limit_delay_source` | `provider`, `backoff` or `None` |
| `rate_limit_wait_exceeded` | The selected delay exceeded the maximum wait |

Successful fallback attempts contain `fallback_failed` (`False`),
`fallback_candidate_name`, `fallback_candidate_index` and
`fallback_failure_count` (candidates that failed before the successful one).
Failed fallback attempts contain `fallback_failed` (`True`) and
`fallback_failure_count`.

`ExecutionAttempt.fallback` is `True` for the fallback attempt.
