from __future__ import annotations

from datetime import UTC, datetime

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from relprim import (
    EventEmitter,
    EventType,
    StructuredEvent,
    async_operation,
)
from relprim.opentelemetry import OpenTelemetryEventSink


def fixed_timestamp() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    tracer = provider.get_tracer("tests.relprim")

    yield tracer, exporter

    provider.shutdown()


async def test_opentelemetry_event_sink_exports_event_to_current_span(
    tracing,
) -> None:
    tracer, exporter = tracing
    sink = OpenTelemetryEventSink()

    event = StructuredEvent(
        event_type=EventType.RETRY_SCHEDULED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
        payload={
            "attempt_number": 1,
            "next_attempt_number": 2,
            "delay_seconds": 0.5,
            "rate_limited": False,
        },
    )

    with tracer.start_as_current_span("request"):
        await sink.emit(event)

    spans = exporter.get_finished_spans()

    assert len(spans) == 1
    assert len(spans[0].events) == 1

    span_event = spans[0].events[0]
    attributes = dict(span_event.attributes or {})

    assert span_event.name == "relprim.retry.scheduled"
    assert span_event.timestamp == int(fixed_timestamp().timestamp() * 1_000_000_000)

    assert attributes == {
        "relprim.event.type": "retry.scheduled",
        "relprim.operation.name": "generate_response",
        "relprim.payload.attempt_number": 1,
        "relprim.payload.next_attempt_number": 2,
        "relprim.payload.delay_seconds": 0.5,
        "relprim.payload.rate_limited": False,
    }


async def test_opentelemetry_event_sink_omits_none_payload_values(
    tracing,
) -> None:
    tracer, exporter = tracing
    sink = OpenTelemetryEventSink()

    event = StructuredEvent(
        event_type=EventType.RATE_LIMIT_DETECTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
        payload={
            "attempt_number": 1,
            "retry_after_seconds": None,
        },
    )

    with tracer.start_as_current_span("request"):
        await sink.emit(event)

    span_event = exporter.get_finished_spans()[0].events[0]
    attributes = dict(span_event.attributes or {})

    assert attributes["relprim.payload.attempt_number"] == 1
    assert "relprim.payload.retry_after_seconds" not in attributes


async def test_opentelemetry_event_sink_is_noop_without_recording_span() -> None:
    sink = OpenTelemetryEventSink(
        span_getter=lambda: trace.INVALID_SPAN,
    )

    event = StructuredEvent(
        event_type=EventType.OPERATION_STARTED,
        operation_name="generate_response",
        timestamp=fixed_timestamp(),
    )

    await sink.emit(event)


async def test_opentelemetry_event_sink_exports_operation_lifecycle(
    tracing,
) -> None:
    tracer, exporter = tracing
    sink = OpenTelemetryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    async def operation() -> str:
        return "ok"

    with tracer.start_as_current_span("request"):
        result = await async_operation("generate_response", operation).with_events(emitter).run()

    assert result.value == "ok"

    span = exporter.get_finished_spans()[0]

    assert [event.name for event in span.events] == [
        "relprim.operation.started",
        "relprim.attempt.started",
        "relprim.attempt.succeeded",
        "relprim.operation.succeeded",
    ]
