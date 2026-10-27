"""Pin the documented structured-event and report-metadata contract.

docs/events-and-metadata.md documents these keys as compatibility-sensitive.
Every emitted payload must contain the guaranteed keys and may only contain
documented keys.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from relprim import (
    AsyncOperation,
    CircuitBreaker,
    EventEmitter,
    EventType,
    ExecutionReport,
    InMemoryEventSink,
    InMemoryIdempotencyStore,
    OperationExecutionError,
    RateLimitPolicy,
    RetryPolicy,
    StructuredEvent,
    async_operation,
    fallback_chain,
    idempotency_policy,
    validation_policy,
    validator,
)

VALIDATION = {
    "validation_performed",
    "validation_valid",
    "validation_validator_name",
    "validation_reason",
}
ATTEMPT_COMMON = {"fallback_used", "circuit_breaker_open", *VALIDATION}
RATE_LIMIT_ATTEMPT = {
    "rate_limited",
    "rate_limit_retry_after_seconds",
    "rate_limit_delay_seconds",
    "rate_limit_delay_source",
    "rate_limit_wait_exceeded",
}
RATE_LIMIT_SUMMARY = {
    "rate_limit_encountered",
    "rate_limit_wait_seconds",
    "rate_limit_wait_exceeded",
}
FALLBACK_SUCCESS = {
    "fallback_failed",
    "fallback_candidate_name",
    "fallback_candidate_index",
    "fallback_failure_count",
}
FALLBACK_FAILURE = {"fallback_failed", "fallback_failure_count"}
OPERATION_SUMMARY = {
    "duration_seconds",
    "attempt_count",
    "retry_count",
    *ATTEMPT_COMMON,
    *RATE_LIMIT_SUMMARY,
}

# event type -> (guaranteed keys, additional keys that may appear)
EVENT_CONTRACT: dict[EventType, tuple[set[str], set[str]]] = {
    EventType.OPERATION_STARTED: (
        {
            "retry_enabled",
            "timeout_enabled",
            "fallback_enabled",
            "circuit_breaker_enabled",
            "validation_enabled",
            "rate_limit_enabled",
        },
        set(),
    ),
    EventType.ATTEMPT_STARTED: ({"attempt_number", "max_attempts"}, set()),
    EventType.ATTEMPT_SUCCEEDED: (
        {"attempt_number", "duration_seconds", *ATTEMPT_COMMON},
        FALLBACK_SUCCESS,
    ),
    EventType.ATTEMPT_FAILED: (
        {"attempt_number", "duration_seconds", "error_type", "retryable", *ATTEMPT_COMMON},
        RATE_LIMIT_ATTEMPT | FALLBACK_FAILURE,
    ),
    EventType.RETRY_SCHEDULED: (
        {
            "attempt_number",
            "next_attempt_number",
            "delay_seconds",
            "delay_source",
            "rate_limited",
            "retry_after_seconds",
        },
        set(),
    ),
    EventType.VALIDATION_SUCCEEDED: (
        {"attempt_number", "validator_name"},
        {"fallback_used", "fallback_candidate_name"},
    ),
    EventType.VALIDATION_FAILED: (
        {"attempt_number", "validator_name", "reason"},
        {"fallback_used", "fallback_candidate_name"},
    ),
    EventType.CIRCUIT_BREAKER_REJECTED: (
        {"attempt_number", "breaker_name", "state", "retry_after_seconds"},
        set(),
    ),
    EventType.RATE_LIMIT_DETECTED: (
        {
            "attempt_number",
            "error_type",
            "retry_after_seconds",
            "max_wait_seconds",
            "max_wait_exceeded",
        },
        set(),
    ),
    EventType.RATE_LIMIT_WAIT_EXCEEDED: (
        {"attempt_number", "retry_after_seconds", "delay_seconds", "max_wait_seconds"},
        set(),
    ),
    EventType.FALLBACK_STARTED: ({"attempt_number", "after_primary_attempts"}, set()),
    EventType.FALLBACK_SUCCEEDED: (
        {"attempt_number", "duration_seconds", *ATTEMPT_COMMON, *FALLBACK_SUCCESS},
        set(),
    ),
    EventType.FALLBACK_FAILED: (
        {"attempt_number", "duration_seconds", "error_type", *ATTEMPT_COMMON, *FALLBACK_FAILURE},
        set(),
    ),
    EventType.OPERATION_SUCCEEDED: (OPERATION_SUMMARY, FALLBACK_SUCCESS),
    EventType.OPERATION_FAILED: (
        {*OPERATION_SUMMARY, "error_type"},
        (RATE_LIMIT_ATTEMPT - {"rate_limit_wait_exceeded"}) | FALLBACK_FAILURE,
    ),
}

REPORT_GUARANTEED = {*ATTEMPT_COMMON, *RATE_LIMIT_SUMMARY}
REPORT_OPTIONAL = (
    FALLBACK_SUCCESS
    | RATE_LIMIT_ATTEMPT
    | {"idempotency_enabled", "idempotency_status", "idempotency_cache_hit"}
)


class ProviderRateLimitError(Exception):
    pass


async def no_sleep(delay: float) -> None:
    return None


def scenarios() -> list[AsyncOperation[[], str]]:
    def counter_failing_first(times: int, exception: Exception) -> Callable[[], Awaitable[str]]:
        calls = 0

        async def operation() -> str:
            nonlocal calls
            calls += 1

            if calls <= times:
                raise exception

            return "ok"

        return operation

    async def ok() -> str:
        return "ok"

    async def blank() -> str:
        return " "

    async def failing() -> str:
        raise ValueError("down")

    non_empty = validation_policy(
        validator("non_empty", lambda value: bool(value.strip()), message="must not be empty")
    )
    retry = RetryPolicy(max_attempts=2, async_sleeper=no_sleep)

    def rate_limits(delay: float, max_wait: float = 60) -> RateLimitPolicy:
        return RateLimitPolicy(
            rate_limit_on=(ProviderRateLimitError,),
            retry_after=lambda exception: delay,
            max_wait_seconds=max_wait,
        )

    return [
        async_operation("op", ok).with_validation(non_empty),
        async_operation("op", blank).with_validation(non_empty).with_retry(retry),
        async_operation("op", counter_failing_first(1, ProviderRateLimitError()))
        .with_retry(retry)
        .with_rate_limit(rate_limits(1)),
        async_operation("op", counter_failing_first(1, ProviderRateLimitError()))
        .with_retry(retry)
        .with_rate_limit(rate_limits(99, max_wait=1)),
        async_operation("op", failing)
        .with_validation(non_empty)
        .with_fallbacks(fallback_chain(("blank", blank), ("ok", ok))),
        async_operation("op", failing).with_fallbacks(fallback_chain(("failing", failing))),
        async_operation("op", failing)
        .with_circuit_breaker(CircuitBreaker(failure_threshold=1))
        .with_retry(retry),
        async_operation("op", ok).with_idempotency(
            idempotency_policy(lambda: "key", store=InMemoryIdempotencyStore())
        ),
    ]


async def collect() -> tuple[list[StructuredEvent], list[ExecutionReport]]:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))
    reports: list[ExecutionReport] = []

    for operation in scenarios():
        try:
            result = await operation.with_events(emitter).run()
            reports.append(result.report)
        except OperationExecutionError as exc:
            reports.append(exc.report)

    return list(await sink.events()), reports


async def test_every_event_type_is_covered_by_the_contract() -> None:
    events, _ = await collect()

    assert set(EVENT_CONTRACT) == set(EventType)
    assert {event.event_type for event in events} == set(EventType)


async def test_event_payloads_match_the_documented_contract() -> None:
    events, _ = await collect()

    for event in events:
        guaranteed, optional = EVENT_CONTRACT[event.event_type]
        keys = set(event.payload)

        assert guaranteed <= keys, (event.event_type, guaranteed - keys)
        assert keys <= guaranteed | optional, (event.event_type, keys - guaranteed - optional)


async def test_report_metadata_matches_the_documented_contract() -> None:
    _, reports = await collect()

    for report in reports:
        keys = set(report.metadata)

        assert keys >= REPORT_GUARANTEED, REPORT_GUARANTEED - keys
        assert keys <= REPORT_GUARANTEED | REPORT_OPTIONAL, (
            keys - REPORT_GUARANTEED - REPORT_OPTIONAL
        )
        assert "idempotency_key" not in keys


@pytest.mark.parametrize("event_type", list(EventType))
def test_event_type_values_are_stable(event_type: EventType) -> None:
    documented = {
        "attempt.started",
        "attempt.succeeded",
        "attempt.failed",
        "circuit_breaker.rejected",
        "fallback.started",
        "fallback.succeeded",
        "fallback.failed",
        "operation.started",
        "operation.succeeded",
        "operation.failed",
        "rate_limit.detected",
        "rate_limit.wait_exceeded",
        "retry.scheduled",
        "validation.succeeded",
        "validation.failed",
    }

    assert event_type.value in documented
