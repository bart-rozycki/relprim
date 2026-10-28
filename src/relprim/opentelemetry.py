from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias

try:
    from opentelemetry import trace
    from opentelemetry.trace import Span
    from opentelemetry.util.types import AttributeValue
except ModuleNotFoundError as exc:
    if exc.name is None or exc.name.split(".")[0] != "opentelemetry":
        raise

    raise ModuleNotFoundError(
        "relprim.opentelemetry requires the optional 'opentelemetry' package. "
        "Install it with: pip install 'relprim[otel]'",
        name=exc.name,
    ) from exc

from relprim.events import StructuredEvent

SpanGetter: TypeAlias = Callable[[], Span]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class OpenTelemetryEventSink:
    """Export RelPrim structured events to the active OpenTelemetry span.

    The sink adds each StructuredEvent as a span event to the currently active
    recording span.

    It does not configure a TracerProvider, SDK, span processor or exporter.
    Those responsibilities remain with the application.

    When no recording span is active, emit() is a no-op.

    Payload values equal to None are omitted because OpenTelemetry attributes
    cannot be None. All other payload values are JSON scalars and therefore
    valid attribute values. The event's own timestamp is used, converted to
    integer nanoseconds without floating-point rounding.
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
    delta = timestamp - _EPOCH
    seconds = delta.days * 86_400 + delta.seconds
    return seconds * 1_000_000_000 + delta.microseconds * 1_000


__all__ = [
    "OpenTelemetryEventSink",
]
