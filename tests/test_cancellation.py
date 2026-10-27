"""Cancellation semantics.

Cancellation of the task running a RelPrim operation must propagate as
asyncio.CancelledError without being retried, falling back, producing a report
or leaving shared state (breakers, idempotency keys) occupied.

Every test synchronizes with asyncio.Event instead of sleeping.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from relprim import (
    CircuitBreaker,
    CircuitBreakerState,
    EventEmitter,
    EventType,
    InMemoryEventSink,
    InMemoryIdempotencyStore,
    OperationTimeoutError,
    RateLimitPolicy,
    RetryPolicy,
    SQLiteEventStore,
    StructuredEvent,
    TimeoutPolicy,
    async_operation,
    fallback_chain,
    idempotency_policy,
)
from relprim.opentelemetry import OpenTelemetryEventSink


class TransientError(Exception):
    pass


class ProviderRateLimitError(Exception):
    pass


class ManualClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def blocking_operation(started: asyncio.Event) -> Callable[..., Awaitable[str]]:
    async def operation(*args: object, **kwargs: object) -> str:
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    return operation


async def cancel_after(task: asyncio.Task[Any], started: asyncio.Event) -> None:
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def assert_no_leaked_tasks() -> None:
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_cancelling_primary_operation_is_not_retried_or_fallen_back() -> None:
    started = asyncio.Event()
    sink = InMemoryEventSink()
    primary_calls = 0
    fallback_calls = 0

    async def primary() -> str:
        nonlocal primary_calls
        primary_calls += 1
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    async def fallback() -> str:
        nonlocal fallback_calls
        fallback_calls += 1
        return "fallback"

    operation = (
        async_operation("provider_call", primary)
        .with_retry(RetryPolicy(max_attempts=3))
        .with_fallbacks(fallback_chain(("fallback", fallback)))
        .with_events(EventEmitter(sinks=(sink,)))
    )

    await cancel_after(asyncio.create_task(operation.run()), started)

    assert primary_calls == 1
    assert fallback_calls == 0
    assert [event.event_type for event in await sink.events()] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
    ]
    assert_no_leaked_tasks()


async def test_cancelling_during_retry_backoff_stops_retrying() -> None:
    sleeping = asyncio.Event()
    calls = 0

    async def blocking_sleeper(delay: float) -> None:
        sleeping.set()
        await asyncio.Event().wait()

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise TransientError("temporary")

    configured = async_operation("provider_call", operation).with_retry(
        RetryPolicy(max_attempts=5, async_sleeper=blocking_sleeper)
    )

    await cancel_after(asyncio.create_task(configured.run()), sleeping)

    assert calls == 1
    assert_no_leaked_tasks()


async def test_cancelling_during_rate_limit_wait_stops_retrying() -> None:
    sleeping = asyncio.Event()
    delays: list[float] = []
    calls = 0

    async def blocking_sleeper(delay: float) -> None:
        delays.append(delay)
        sleeping.set()
        await asyncio.Event().wait()

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ProviderRateLimitError("slow down")

    configured = (
        async_operation("provider_call", operation)
        .with_retry(RetryPolicy(max_attempts=3, async_sleeper=blocking_sleeper))
        .with_rate_limit(
            RateLimitPolicy(
                rate_limit_on=(ProviderRateLimitError,),
                retry_after=lambda exception: 2.0,
            )
        )
    )

    await cancel_after(asyncio.create_task(configured.run()), sleeping)

    assert calls == 1
    assert delays == [2.0]


async def test_cancelling_inside_timeout_policy_is_not_reported_as_timeout() -> None:
    started = asyncio.Event()
    policy = TimeoutPolicy(seconds=60)

    task = asyncio.create_task(policy.run_async(blocking_operation(started)))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task

    assert not isinstance(exc_info.value, OperationTimeoutError)


async def test_outer_deadline_is_not_converted_into_relprim_timeout() -> None:
    started = asyncio.Event()

    configured = (
        async_operation("provider_call", blocking_operation(started))
        .with_retry(RetryPolicy(max_attempts=3))
        .with_timeout(TimeoutPolicy(seconds=60))
    )

    with pytest.raises(TimeoutError) as exc_info:
        async with asyncio.timeout(None) as outer:

            async def expire_once_started() -> None:
                await started.wait()
                outer.reschedule(asyncio.get_running_loop().time())

            trigger = asyncio.create_task(expire_once_started())
            await configured.run()

    await trigger
    # The caller's own deadline expired: RelPrim neither retried nor reported
    # it as its own timeout.
    assert not isinstance(exc_info.value, OperationTimeoutError)
    assert_no_leaked_tasks()


async def test_cancelling_during_fallback_does_not_try_next_candidate() -> None:
    started = asyncio.Event()
    second_calls = 0

    async def primary() -> str:
        raise TransientError("primary down")

    async def second() -> str:
        nonlocal second_calls
        second_calls += 1
        return "second"

    configured = async_operation("provider_call", primary).with_fallbacks(
        fallback_chain(
            ("first", blocking_operation(started)),
            ("second", second),
        )
    )

    await cancel_after(asyncio.create_task(configured.run()), started)

    assert second_calls == 0


async def test_cancelling_during_event_delivery_propagates() -> None:
    delivering = asyncio.Event()
    calls = 0

    class BlockingSink:
        async def emit(self, event: StructuredEvent) -> None:
            if event.event_type is EventType.ATTEMPT_SUCCEEDED:
                delivering.set()
                await asyncio.Event().wait()

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    store = InMemoryIdempotencyStore()
    configured = (
        async_operation("provider_call", operation)
        .with_retry(RetryPolicy(max_attempts=3))
        .with_events(EventEmitter(sinks=(BlockingSink(),)))
        .with_idempotency(idempotency_policy(lambda: "key", store=store))
    )

    await cancel_after(asyncio.create_task(configured.run()), delivering)

    assert calls == 1
    # The cancelled execution is not cached and does not keep the key occupied.
    assert await store.entry_count() == 0


async def test_cancelled_half_open_probe_releases_probe_slot() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=10, clock=clock)

    async def failing() -> str:
        raise TransientError("down")

    async def succeeding() -> str:
        return "ok"

    with pytest.raises(TransientError):
        await breaker.run_async(failing)

    clock.advance(10)
    started = asyncio.Event()

    await cancel_after(
        asyncio.create_task(breaker.run_async(blocking_operation(started))),
        started,
    )

    snapshot = await breaker.snapshot()
    assert snapshot.state is CircuitBreakerState.HALF_OPEN
    assert snapshot.half_open_probe_in_flight is False

    assert await breaker.run_async(succeeding) == "ok"
    assert (await breaker.snapshot()).state is CircuitBreakerState.CLOSED


async def test_cancellation_in_closed_state_is_not_counted_as_failure() -> None:
    breaker = CircuitBreaker(failure_threshold=1)
    started = asyncio.Event()

    await cancel_after(
        asyncio.create_task(breaker.run_async(blocking_operation(started))),
        started,
    )

    snapshot = await breaker.snapshot()
    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0


async def test_cancelled_operation_through_breaker_keeps_breaker_usable() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=10, clock=clock)

    async def failing() -> str:
        raise TransientError("down")

    with pytest.raises(TransientError):
        await breaker.run_async(failing)

    clock.advance(10)
    started = asyncio.Event()
    configured = async_operation("provider_call", blocking_operation(started)).with_circuit_breaker(
        breaker
    )

    await cancel_after(asyncio.create_task(configured.run()), started)

    async def succeeding() -> str:
        return "ok"

    result = await async_operation("provider_call", succeeding).with_circuit_breaker(breaker).run()

    assert result.value == "ok"
    assert (await breaker.snapshot()).state is CircuitBreakerState.CLOSED


async def test_owner_cancellation_hands_execution_to_joined_caller() -> None:
    store = InMemoryIdempotencyStore()
    owner_started = asyncio.Event()
    calls = 0

    async def owner_operation() -> str:
        owner_started.set()
        await asyncio.Event().wait()
        return "unreachable"

    async def waiter_operation() -> str:
        nonlocal calls
        calls += 1
        return "created"

    owner = asyncio.create_task(store.execute("order-1", owner_operation, ttl_seconds=60))
    await owner_started.wait()

    waiter = asyncio.create_task(store.execute("order-1", waiter_operation, ttl_seconds=60))
    # One scheduling step lets the waiter join the in-flight execution.
    await asyncio.sleep(0)

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    result = await waiter

    assert result.value == "created"
    assert result.executed
    assert calls == 1
    assert_no_leaked_tasks()


async def test_concurrent_waiters_survive_one_waiter_cancellation() -> None:
    store = InMemoryIdempotencyStore()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "created"

    owner = asyncio.create_task(store.execute("order-1", operation, ttl_seconds=60))
    await started.wait()

    waiters = [
        asyncio.create_task(store.execute("order-1", operation, ttl_seconds=60)) for _ in range(3)
    ]
    await asyncio.sleep(0)

    waiters[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiters[0]

    release.set()

    owner_result = await owner
    results = [await waiter for waiter in waiters[1:]]

    assert owner_result.executed
    assert [result.joined for result in results] == [True, True]
    assert {result.value for result in results} == {"created"}
    assert calls == 1


async def test_cancelled_sqlite_emit_does_not_break_the_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")
    await store.initialize()

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_emit_sync = SQLiteEventStore._emit_sync

    def gated_emit_sync(self: SQLiteEventStore, event: StructuredEvent) -> None:
        entered.set()
        release.wait()
        try:
            original_emit_sync(self, event)
        finally:
            finished.set()

    monkeypatch.setattr(SQLiteEventStore, "_emit_sync", gated_emit_sync)

    event = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="provider_call",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )

    task = asyncio.create_task(store.emit(event))
    await asyncio.to_thread(entered.wait)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    release.set()
    await asyncio.to_thread(finished.wait)
    monkeypatch.undo()

    # The statement already running in the worker thread completed.
    history = await store.history()
    assert [stored.event for stored in history] == [event]

    await store.emit(event)
    assert len(await store.history()) == 2


async def test_cancelled_operation_exports_only_events_emitted_before_cancellation() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("tests.relprim")
    started = asyncio.Event()

    configured = async_operation("provider_call", blocking_operation(started)).with_events(
        EventEmitter(sinks=(OpenTelemetryEventSink(),))
    )

    async def traced() -> object:
        with tracer.start_as_current_span("request"):
            return await configured.run()

    await cancel_after(asyncio.create_task(traced()), started)

    (span,) = exporter.get_finished_spans()
    assert [event.name for event in span.events] == [
        "relprim.operation.started",
        "relprim.attempt.started",
    ]
    provider.shutdown()
