from __future__ import annotations

from datetime import UTC, datetime

import pytest

from relprim import (
    EventEmitter,
    EventType,
    InMemoryEventSink,
    NoopEventSink,
    StructuredEvent,
)


def fixed_timestamp() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_structured_event_can_be_serialized_to_dict() -> None:
    event = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
        payload={"attempt": 1},
    )

    assert event.to_dict() == {
        "event_type": "operation.started",
        "operation_name": "generate_response",
        "timestamp": "2026-01-01T12:00:00+00:00",
        "payload": {"attempt": 1},
    }


def test_structured_event_rejects_empty_operation_name() -> None:
    with pytest.raises(ValueError):
        StructuredEvent(
            event_type=EventType.OPERATION_STARTED,
            operation_name=" ",
            timestamp=fixed_timestamp(),
        )


def test_structured_event_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError):
        StructuredEvent(
            event_type=EventType.OPERATION_STARTED,
            operation_name="generate_response",
            timestamp=datetime(2026, 1, 1, 12, 0),
        )


def test_structured_event_freezes_payload() -> None:
    payload = {"provider": "primary"}

    event = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
        payload=payload,
    )

    payload["provider"] = "fallback"

    assert event.payload["provider"] == "primary"

    with pytest.raises(TypeError):
        event.payload["provider"] = "other"  # type: ignore[index]


async def test_in_memory_event_sink_stores_events_in_order() -> None:
    sink = InMemoryEventSink()

    first = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
    )
    second = StructuredEvent(
        event_type=EventType.OPERATION_SUCCEEDED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
    )

    await sink.emit(first)
    await sink.emit(second)

    assert await sink.events() == (first, second)


async def test_in_memory_event_sink_clear_removes_events() -> None:
    sink = InMemoryEventSink()

    event = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
    )

    await sink.emit(event)
    await sink.clear()

    assert await sink.events() == ()


async def test_noop_event_sink_discards_events() -> None:
    sink = NoopEventSink()

    event = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
    )

    await sink.emit(event)


async def test_event_emitter_creates_and_emits_event() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(
        sinks=(sink,),
        timestamp_factory=fixed_timestamp,
    )

    event = await emitter.emit(
        EventType.OPERATION_STARTED,
        operation_name="generate_response",
        payload={"provider": "primary"},
    )

    events = await sink.events()

    assert events == (event,)
    assert event.event_type is EventType.OPERATION_STARTED
    assert event.operation_name == "generate_response"
    assert event.timestamp == fixed_timestamp()
    assert event.payload == {"provider": "primary"}


async def test_event_emitter_emits_to_multiple_sinks() -> None:
    first_sink = InMemoryEventSink()
    second_sink = InMemoryEventSink()

    emitter = EventEmitter(
        sinks=(first_sink, second_sink),
        timestamp_factory=fixed_timestamp,
    )

    event = await emitter.emit(
        EventType.OPERATION_STARTED,
        operation_name="generate_response",
    )

    assert await first_sink.events() == (event,)
    assert await second_sink.events() == (event,)


async def test_event_emitter_uses_noop_sink_by_default() -> None:
    emitter = EventEmitter(timestamp_factory=fixed_timestamp)

    event = await emitter.emit(
        EventType.OPERATION_STARTED,
        operation_name="generate_response",
    )

    assert event.event_type is EventType.OPERATION_STARTED
    assert event.operation_name == "generate_response"


def test_event_emitter_requires_at_least_one_sink() -> None:
    with pytest.raises(ValueError):
        EventEmitter(sinks=())
