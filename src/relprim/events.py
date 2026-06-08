from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
EventPayload: TypeAlias = Mapping[str, JsonScalar]
TimestampFactory: TypeAlias = Callable[[], datetime]


class EventType(StrEnum):
    """Structured event type emitted by RelPrim components."""

    OPERATION_STARTED = "operation.started"
    OPERATION_SUCCEEDED = "operation.succeeded"
    OPERATION_FAILED = "operation.failed"

    ATTEMPT_STARTED = "attempt.started"
    ATTEMPT_SUCCEEDED = "attempt.succeeded"
    ATTEMPT_FAILED = "attempt.failed"

    RETRY_SCHEDULED = "retry.scheduled"

    FALLBACK_STARTED = "fallback.started"
    FALLBACK_SUCCEEDED = "fallback.succeeded"
    FALLBACK_FAILED = "fallback.failed"

    CIRCUIT_BREAKER_REJECTED = "circuit_breaker.rejected"

    VALIDATION_SUCCEEDED = "validation.succeeded"
    VALIDATION_FAILED = "validation.failed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _empty_payload() -> EventPayload:
    return MappingProxyType({})


def _freeze_payload(payload: Mapping[str, JsonScalar] | None) -> EventPayload:
    return MappingProxyType(dict(payload or {}))


@dataclass(frozen=True, slots=True)
class StructuredEvent:
    """Immutable structured event emitted by RelPrim.

    Events are intentionally serializable and transport-agnostic. They can be
    sent to logs, in-memory test sinks, SQLite stores, OpenTelemetry exporters or
    other observability systems.
    """

    event_type: EventType
    operation_name: str
    timestamp: datetime
    payload: EventPayload = field(default_factory=_empty_payload)

    def __post_init__(self) -> None:
        if not self.operation_name.strip():
            raise ValueError("operation_name must not be empty.")

        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware.")

        object.__setattr__(self, "payload", _freeze_payload(self.payload))

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type.value,
            "operation_name": self.operation_name,
            "timestamp": self.timestamp.isoformat(),
            "payload": dict(self.payload),
        }


class EventSink(Protocol):
    """Protocol implemented by async structured event sinks."""

    async def emit(self, event: StructuredEvent) -> None:
        """Persist, export or forward a structured event."""


@dataclass(frozen=True, slots=True)
class NoopEventSink:
    """Event sink that intentionally discards all events."""

    async def emit(self, event: StructuredEvent) -> None:
        return None


@dataclass(slots=True)
class InMemoryEventSink:
    """In-memory event sink for tests, demos and local inspection.

    The sink is concurrency-safe for asyncio workloads.
    """

    _events: list[StructuredEvent] = field(default_factory=list, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def emit(self, event: StructuredEvent) -> None:
        async with self._lock:
            self._events.append(event)

    async def events(self) -> tuple[StructuredEvent, ...]:
        async with self._lock:
            return tuple(self._events)

    async def clear(self) -> None:
        async with self._lock:
            self._events.clear()


@dataclass(frozen=True, slots=True)
class EventEmitter:
    """Small structured event emitter.

    EventEmitter creates immutable StructuredEvent objects and forwards them to
    configured sinks.

    It intentionally does not run hidden background tasks, swallow sink errors or
    buffer events implicitly. If a sink fails, the caller sees that failure.
    Higher-level integrations may later decide whether event delivery failures
    should fail the operation or be isolated.
    """

    sinks: tuple[EventSink, ...] = field(default_factory=lambda: (NoopEventSink(),))
    timestamp_factory: TimestampFactory = _utc_now

    def __post_init__(self) -> None:
        if not self.sinks:
            raise ValueError("sinks must contain at least one event sink.")

    async def emit(
        self,
        event_type: EventType,
        *,
        operation_name: str,
        payload: Mapping[str, JsonScalar] | None = None,
    ) -> StructuredEvent:
        event = StructuredEvent(
            event_type=event_type,
            operation_name=operation_name,
            timestamp=self.timestamp_factory(),
            payload=payload or {},
        )

        for sink in self.sinks:
            await sink.emit(event)

        return event
