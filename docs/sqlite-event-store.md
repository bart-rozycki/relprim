# SQLite event store

RelPrim can persist structured reliability events in a local SQLite database.

`SQLiteEventStore` implements the standard `EventSink` protocol, so it works
with the existing `EventEmitter` without changing operation configuration.

The store also provides basic history queries and timestamp-based retention.

## Basic usage

```python
from relprim import (
    EventEmitter,
    SQLiteEventStore,
    resilient,
)


event_store = SQLiteEventStore("relprim-events.db")

event_emitter = EventEmitter(
    sinks=(event_store,)
)


@resilient(
    retries=3,
    timeout=10,
    events=event_emitter,
)
async def call_provider(prompt: str) -> str:
    return await provider.generate(prompt)
```

The database and schema are initialized lazily when the first event is emitted.

Applications that prefer startup-time validation can initialize explicitly:

```python
await event_store.initialize()
```

## Reading event history

Use `history()` to inspect persisted events:

```python
history = await event_store.history(
    limit=100,
)

for stored in history:
    print(stored.sequence_id)
    print(stored.event.to_dict())
```

Each result is a `StoredEvent` containing:

```python
stored.sequence_id
stored.event
```

The sequence ID is assigned by SQLite and provides stable ordering for history
queries.

## Filter by operation

```python
history = await event_store.history(
    operation_name="generate_response",
)
```

Only events emitted by that operation are returned.

## Filter by event type

```python
from relprim import EventType


history = await event_store.history(
    event_types=(
        EventType.ATTEMPT_FAILED,
        EventType.RETRY_SCHEDULED,
    ),
)
```

Operation-name and event-type filters can be combined.

## Sequence pagination

Use `after_sequence_id` to continue from a previous page:

```python
first_page = await event_store.history(
    limit=100,
)

last_sequence_id = first_page[-1].sequence_id

next_page = await event_store.history(
    after_sequence_id=last_sequence_id,
    limit=100,
)
```

Results are always ordered by ascending sequence ID.

## Retention

Old events can be removed using a timezone-aware timestamp:

```python
from datetime import UTC, datetime, timedelta


cutoff = datetime.now(UTC) - timedelta(days=30)

deleted = await event_store.delete_before(
    cutoff
)

print(f"Deleted {deleted} events")
```

Events strictly older than the supplied timestamp are deleted.

RelPrim does not run retention automatically or create background cleanup
tasks. Applications decide when retention should run.

## Multiple sinks

SQLite persistence can be combined with other event sinks.

For example, when the OpenTelemetry integration is installed:

```python
from relprim import (
    EventEmitter,
    SQLiteEventStore,
)
from relprim.opentelemetry import (
    OpenTelemetryEventSink,
)


event_emitter = EventEmitter(
    sinks=(
        SQLiteEventStore("relprim-events.db"),
        OpenTelemetryEventSink(),
    )
)
```

The same RelPrim event is persisted locally and added to the active
OpenTelemetry span.

Sinks are awaited in their configured order.

## Concurrency

SQLite operations are executed outside the asyncio event loop.

The store creates a separate SQLite connection for each database operation and
uses WAL mode plus a configurable SQLite busy timeout.

```python
store = SQLiteEventStore(
    "relprim-events.db",
    busy_timeout_seconds=5,
)
```

The default busy timeout is five seconds.

SQLite still serializes concurrent writers. The store is intended for normal
application event volumes rather than high-throughput distributed telemetry
pipelines.

## Schema management

The event store maintains its own schema version in the `relprim_schema` table.

The initial schema version is `1`.

Schema initialization and migrations happen automatically before the first
database operation.

A database created by a newer unsupported RelPrim schema version raises
`SQLiteEventStoreSchemaError` instead of attempting an unsafe downgrade.

## Database format

Structured events are stored with:

- sequence ID;
- event type;
- operation name;
- timezone-aware timestamp;
- JSON payload.

Event payloads must contain JSON-compatible scalar values.

Non-finite floating-point values are rejected instead of being persisted as
non-standard JSON.

## Why `:memory:` is not supported

`SQLiteEventStore` creates a fresh SQLite connection for each database
operation.

A normal `:memory:` SQLite database belongs to a single connection, so later
operations would not see previously stored events.

Use a temporary filesystem database for tests instead.

## Failure behavior

`SQLiteEventStore` follows the normal `EventEmitter` behavior.

Event delivery is explicit and awaited. RelPrim does not swallow sink failures
or move persistence into a hidden background task.

If SQLite event persistence fails, that sink error is visible to the caller.

Applications that do not want event-storage failures to affect business
operations should choose their event delivery architecture accordingly.

## Scope and limitations

`SQLiteEventStore` is intended for:

- local development;
- desktop applications;
- local workers;
- single-host services;
- debugging and execution history;
- lightweight durable event persistence.

It is not:

- a distributed event store;
- an event-sourcing framework;
- a message broker;
- an observability backend;
- a replacement for OpenTelemetry;
- intended for SQLite files on network filesystems.

For distributed observability, export RelPrim events through OpenTelemetry or
another external event sink.
