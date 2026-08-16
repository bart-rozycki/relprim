from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from opentelemetry import trace
from opentelemetry.trace import Span
from opentelemetry.util.types import AttributeValue

from relprim.events import StructuredEvent

SpanGetter: TypeAlias = Callable[[], Span]


@dataclass(frozen=True, slots=True)
class OpenTelemetryEventSink:
    """Export RelPrim structured events to the active OpenTelemetry span.

    The sink adds each StructuredEvent as a span event to the currently active
    recording span.

    It does not configure a TracerProvider, SDK, span processor or exporter.
    Those responsibilities remain with the application.

    When no recording span is active, emit() is a no-op.
    """

    span_getter: SpanGetter = trace.get_current_span

    async def emit(self, event: StructuredEvent) -> None:
        """Add a RelPrim structured event to the active recording span."""
        span = self.span_getter()

        if not span.is_recording():
            return

        span.add_event(
            name=f"relprim.{event.event_type.value}",
            attributes=_event_attributes(event),
            timestamp=_timestamp_ns(event.timestamp),
        )


def _event_attributes(
    event: StructuredEvent,
) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {
        "relprim.event.type": event.event_type.value,
        "relprim.operation.name": event.operation_name,
    }

    for key, value in event.payload.items():
        if value is None:
            continue

        attributes[f"relprim.payload.{key}"] = value

    return attributes


def _timestamp_ns(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1_000_000_000)


__all__ = [
    "OpenTelemetryEventSink",
]
