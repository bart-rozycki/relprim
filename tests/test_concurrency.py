"""Concurrency invariants of stateful primitives.

The tests pin specific invariants with explicit synchronization instead of
relying on large loops that only make races less likely.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from relprim import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    EventEmitter,
    EventType,
    InMemoryEventSink,
    InMemoryIdempotencyStore,
    SQLiteEventStore,
    StructuredEvent,
    async_operation,
)
from relprim.opentelemetry import OpenTelemetryEventSink


class TransientError(Exception):
    pass


class ManualClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Gate:
    """Lets a test hold N concurrent calls inside an operation, then release them."""

    def __init__(self, expected: int) -> None:
        self.expected = expected
        self.entered = 0
        self.all_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self) -> None:
        self.entered += 1

        if self.entered == self.expected:
            self.all_entered.set()

        await self.release.wait()


async def test_concurrent_failures_open_breaker_exactly_at_threshold() -> None:
    clock = ManualClock(100.0)
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=10, clock=clock)
    gate = Gate(expected=5)

    async def failing() -> str:
        await gate.wait()
        raise TransientError("down")

    calls = [asyncio.create_task(breaker.run_async(failing)) for _ in range(5)]
    await gate.all_entered.wait()
    gate.release.set()

    results = await asyncio.gather(*calls, return_exceptions=True)

    # All five were admitted while closed; all five failed with their own error.
    assert all(isinstance(result, TransientError) for result in results)

    snapshot = await breaker.snapshot()
    assert snapshot.state is CircuitBreakerState.OPEN
    assert snapshot.opened_at == 100.0

    with pytest.raises(CircuitBreakerOpenError):
        await breaker.run_async(failing)


async def test_concurrent_success_resets_failure_count_in_closed_state() -> None:
    breaker = CircuitBreaker(failure_threshold=3)
    gate = Gate(expected=3)

    async def failing() -> str:
        await gate.wait()
        raise TransientError("down")

    async def succeeding() -> str:
        await gate.wait()
        return "ok"

    calls = [
        asyncio.create_task(breaker.run_async(failing)),
        asyncio.create_task(breaker.run_async(failing)),
        asyncio.create_task(breaker.run_async(succeeding)),
    ]
    await gate.all_entered.wait()
    gate.release.set()
    await asyncio.gather(*calls, return_exceptions=True)

    snapshot = await breaker.snapshot()
    # Completion order is FIFO: two failures, then the success resets them.
    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0


async def test_half_open_admits_one_probe_and_rejects_concurrent_calls() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=5, clock=clock)

    async def failing() -> str:
        raise TransientError("down")

    with pytest.raises(TransientError):
        await breaker.run_async(failing)

    clock.advance(5)
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    probe_calls = 0

    async def probe() -> str:
        nonlocal probe_calls
        probe_calls += 1
        probe_started.set()
        await release_probe.wait()
        return "ok"

    probe_task = asyncio.create_task(breaker.run_async(probe))
    await probe_started.wait()

    concurrent = await asyncio.gather(
        *(breaker.run_async(probe) for _ in range(3)),
        return_exceptions=True,
    )

    assert all(isinstance(result, CircuitBreakerOpenError) for result in concurrent)
    assert all(
        result.state is CircuitBreakerState.HALF_OPEN and result.retry_after_seconds is None
        for result in concurrent
        if isinstance(result, CircuitBreakerOpenError)
    )

    release_probe.set()
    assert await probe_task == "ok"
    assert probe_calls == 1
    assert (await breaker.snapshot()).state is CircuitBreakerState.CLOSED


async def test_failed_half_open_probe_reopens_breaker_with_new_open_time() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=5, clock=clock)

    async def failing() -> str:
        raise TransientError("down")

    with pytest.raises(TransientError):
        await breaker.run_async(failing)

    clock.advance(7)

    with pytest.raises(TransientError):
        await breaker.run_async(failing)

    snapshot = await breaker.snapshot()
    assert snapshot.state is CircuitBreakerState.OPEN
    assert snapshot.opened_at == 7
    assert snapshot.half_open_probe_in_flight is False


async def test_in_flight_key_does_not_block_unrelated_keys() -> None:
    store = InMemoryIdempotencyStore()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> str:
        started.set()
        await release.wait()
        return "slow"

    async def fast() -> str:
        return "fast"

    slow_task = asyncio.create_task(store.execute("key-a", slow, ttl_seconds=60))
    await started.wait()

    fast_result = await store.execute("key-b", fast, ttl_seconds=60)

    assert fast_result.value == "fast"
    assert fast_result.executed
    assert not slow_task.done()

    release.set()
    assert (await slow_task).value == "slow"


async def test_owner_failure_reaches_every_joined_caller_and_releases_key() -> None:
    store = InMemoryIdempotencyStore()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def failing() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        raise TransientError("down")

    owner = asyncio.create_task(store.execute("key", failing, ttl_seconds=60))
    await started.wait()
    joined = [asyncio.create_task(store.execute("key", failing, ttl_seconds=60)) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(owner, *joined, return_exceptions=True)

    assert all(isinstance(result, TransientError) for result in results)
    assert calls == 1
    assert await store.entry_count() == 0


async def test_expiry_of_one_key_does_not_affect_other_keys() -> None:
    clock = ManualClock()
    store = InMemoryIdempotencyStore(clock=clock)
    calls: list[str] = []

    def operation_for(name: str):
        async def operation() -> str:
            calls.append(name)
            return name

        return operation

    await store.execute("short", operation_for("short"), ttl_seconds=5)
    await store.execute("long", operation_for("long"), ttl_seconds=50)

    clock.advance(5)

    short_again = await store.execute("short", operation_for("short"), ttl_seconds=5)
    long_again = await store.execute("long", operation_for("long"), ttl_seconds=50)

    assert short_again.executed
    assert long_again.replayed
    assert calls == ["short", "long", "short"]


async def test_concurrent_operations_keep_per_operation_event_order() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))
    gate = Gate(expected=3)

    def operation_named(name: str):
        async def operation() -> str:
            await gate.wait()
            return name

        return async_operation(name, operation).with_events(emitter)

    tasks = [asyncio.create_task(operation_named(f"op-{index}").run()) for index in range(3)]
    await gate.all_entered.wait()
    gate.release.set()
    await asyncio.gather(*tasks)

    events = await sink.events()
    assert len(events) == 3 * 4

    for index in range(3):
        own = [event.event_type for event in events if event.operation_name == f"op-{index}"]
        assert own == [
            EventType.OPERATION_STARTED,
            EventType.ATTEMPT_STARTED,
            EventType.ATTEMPT_SUCCEEDED,
            EventType.OPERATION_SUCCEEDED,
        ]


async def test_sqlite_concurrent_writers_and_readers_share_one_database(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    writers = [SQLiteEventStore(database) for _ in range(4)]
    reader = SQLiteEventStore(database)

    def make_event(writer: int, index: int) -> StructuredEvent:
        return StructuredEvent(
            event_type=EventType.ATTEMPT_STARTED,
            operation_name=f"writer-{writer}",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            payload={"index": index},
        )

    async def write_all(writer: int) -> None:
        for index in range(25):
            await writers[writer].emit(make_event(writer, index))

    async def read_while_writing() -> int:
        seen = 0

        for _ in range(10):
            seen = max(seen, len(await reader.history(limit=1000)))

        return seen

    *_, observed = await asyncio.gather(
        *(write_all(writer) for writer in range(4)), read_while_writing()
    )

    history = await reader.history(limit=1000)
    sequence_ids = [stored.sequence_id for stored in history]

    assert len(history) == 100
    assert sequence_ids == sorted(set(sequence_ids))
    assert observed <= 100

    for writer in range(4):
        indexes = [
            stored.event.payload["index"]
            for stored in history
            if stored.event.operation_name == f"writer-{writer}"
        ]
        # Each writer's own events are stored in the order it emitted them.
        assert indexes == list(range(25))


async def test_opentelemetry_sink_does_not_mix_concurrent_operations() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("tests.relprim")
    emitter = EventEmitter(sinks=(OpenTelemetryEventSink(),))
    gate = Gate(expected=2)

    async def traced(name: str) -> None:
        async def operation() -> str:
            await gate.wait()
            return name

        with tracer.start_as_current_span(f"span-{name}"):
            await async_operation(name, operation).with_events(emitter).run()

    tasks = [asyncio.create_task(traced(name)) for name in ("a", "b")]
    await gate.all_entered.wait()
    gate.release.set()
    await asyncio.gather(*tasks)

    spans = {span.name: span for span in exporter.get_finished_spans()}

    for name in ("a", "b"):
        operation_names = {
            event.attributes["relprim.operation.name"]
            for event in spans[f"span-{name}"].events
            if event.attributes is not None
        }
        assert operation_names == {name}

    provider.shutdown()
