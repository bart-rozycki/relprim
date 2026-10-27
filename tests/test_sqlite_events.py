from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from relprim import (
    EventEmitter,
    EventType,
    SQLiteEventStore,
    SQLiteEventStoreSchemaError,
    StructuredEvent,
    async_operation,
)


def event(
    *,
    event_type: EventType = EventType.OPERATION_STARTED,
    operation_name: str = "provider_call",
    timestamp: datetime | None = None,
    payload: dict[str, str | int | float | bool | None] | None = None,
) -> StructuredEvent:
    return StructuredEvent(
        event_type=event_type,
        operation_name=operation_name,
        timestamp=timestamp
        or datetime(
            2026,
            1,
            1,
            12,
            0,
            tzinfo=UTC,
        ),
        payload=payload or {},
    )


async def test_sqlite_event_store_persists_event(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.db"

    store = SQLiteEventStore(database)

    await store.emit(
        event(
            payload={
                "attempt_number": 1,
                "message": "hello",
            }
        )
    )

    reopened_store = SQLiteEventStore(database)

    history = await reopened_store.history()

    assert len(history) == 1

    stored = history[0]

    assert stored.sequence_id == 1
    assert stored.event.event_type is EventType.OPERATION_STARTED
    assert stored.event.operation_name == "provider_call"
    assert stored.event.payload["attempt_number"] == 1
    assert stored.event.payload["message"] == "hello"


async def test_sqlite_event_store_preserves_payload_values(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    await store.emit(
        event(
            payload={
                "text": "zażółć",
                "integer": 3,
                "floating": 1.5,
                "boolean": True,
                "missing": None,
            }
        )
    )

    history = await store.history()

    assert history[0].event.payload == {
        "text": "zażółć",
        "integer": 3,
        "floating": 1.5,
        "boolean": True,
        "missing": None,
    }


async def test_sqlite_event_store_filters_by_operation_name(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    await store.emit(event(operation_name="primary"))
    await store.emit(event(operation_name="backup"))
    await store.emit(event(operation_name="primary"))

    history = await store.history(
        operation_name="primary",
    )

    assert len(history) == 2

    assert {item.event.operation_name for item in history} == {"primary"}


async def test_sqlite_event_store_filters_by_event_type(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    await store.emit(
        event(
            event_type=EventType.ATTEMPT_STARTED,
        )
    )
    await store.emit(
        event(
            event_type=EventType.ATTEMPT_FAILED,
        )
    )
    await store.emit(
        event(
            event_type=EventType.RETRY_SCHEDULED,
        )
    )

    history = await store.history(
        event_types=(
            EventType.ATTEMPT_FAILED,
            EventType.RETRY_SCHEDULED,
        )
    )

    assert [item.event.event_type for item in history] == [
        EventType.ATTEMPT_FAILED,
        EventType.RETRY_SCHEDULED,
    ]


async def test_sqlite_event_store_supports_sequence_pagination(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    for attempt_number in range(1, 6):
        await store.emit(
            event(
                payload={
                    "attempt_number": attempt_number,
                }
            )
        )

    first_page = await store.history(limit=2)

    assert [item.sequence_id for item in first_page] == [1, 2]

    second_page = await store.history(
        after_sequence_id=first_page[-1].sequence_id,
        limit=2,
    )

    assert [item.sequence_id for item in second_page] == [3, 4]


async def test_sqlite_event_store_returns_empty_history_for_empty_event_types(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    history = await store.history(
        event_types=(),
    )

    assert history == ()


async def test_sqlite_event_store_deletes_events_before_timestamp(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    await store.emit(
        event(
            timestamp=datetime(
                2026,
                1,
                1,
                tzinfo=UTC,
            )
        )
    )

    await store.emit(
        event(
            timestamp=datetime(
                2026,
                1,
                2,
                tzinfo=UTC,
            )
        )
    )

    await store.emit(
        event(
            timestamp=datetime(
                2026,
                1,
                3,
                tzinfo=UTC,
            )
        )
    )

    deleted = await store.delete_before(
        datetime(
            2026,
            1,
            3,
            tzinfo=UTC,
        )
    )

    history = await store.history()

    assert deleted == 2
    assert len(history) == 1
    assert history[0].event.timestamp == datetime(
        2026,
        1,
        3,
        tzinfo=UTC,
    )


async def test_sqlite_event_store_supports_concurrent_emits(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(
        tmp_path / "events.db",
        busy_timeout_seconds=5,
    )

    await asyncio.gather(
        *(
            store.emit(
                event(
                    payload={
                        "index": index,
                    }
                )
            )
            for index in range(50)
        )
    )

    history = await store.history(limit=100)

    assert len(history) == 50

    assert [item.sequence_id for item in history] == list(range(1, 51))

    assert {item.event.payload["index"] for item in history} == set(range(50))


async def test_sqlite_event_store_integrates_with_event_emitter(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    emitter = EventEmitter(sinks=(store,))

    async def operation() -> str:
        return "ok"

    result = await (
        async_operation(
            "provider_call",
            operation,
        )
        .with_events(emitter)
        .run()
    )

    history = await store.history(
        operation_name="provider_call",
    )

    assert result.value == "ok"

    assert [item.event.event_type for item in history] == [
        EventType.OPERATION_STARTED,
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_SUCCEEDED,
        EventType.OPERATION_SUCCEEDED,
    ]


async def test_sqlite_event_store_initializes_schema_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.db"

    store = SQLiteEventStore(database)

    await store.initialize()

    connection = sqlite3.connect(database)

    try:
        row = connection.execute(
            """
            SELECT version
            FROM relprim_schema
            WHERE component = 'event_store'
            """
        ).fetchone()

        table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'relprim_events'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (1,)
    assert table == ("relprim_events",)


async def test_sqlite_event_store_migrates_unversioned_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.db"

    connection = sqlite3.connect(database)

    try:
        connection.execute(
            """
            CREATE TABLE relprim_schema (
                component TEXT PRIMARY KEY,
                version INTEGER NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    store = SQLiteEventStore(database)

    await store.initialize()

    connection = sqlite3.connect(database)

    try:
        row = connection.execute(
            """
            SELECT version
            FROM relprim_schema
            WHERE component = 'event_store'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (1,)


async def test_sqlite_event_store_rejects_newer_schema_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.db"

    connection = sqlite3.connect(database)

    try:
        connection.execute(
            """
            CREATE TABLE relprim_schema (
                component TEXT PRIMARY KEY,
                version INTEGER NOT NULL
            )
            """
        )

        connection.execute(
            """
            INSERT INTO relprim_schema (
                component,
                version
            )
            VALUES ('event_store', 999)
            """
        )

        connection.commit()
    finally:
        connection.close()

    store = SQLiteEventStore(database)

    with pytest.raises(
        SQLiteEventStoreSchemaError,
        match="newer than the supported version",
    ):
        await store.initialize()


def test_sqlite_event_store_rejects_memory_database() -> None:
    with pytest.raises(
        ValueError,
        match="does not support ':memory:'",
    ):
        SQLiteEventStore(":memory:")


def test_sqlite_event_store_rejects_invalid_busy_timeout() -> None:
    with pytest.raises(
        ValueError,
        match="greater than or equal to 0",
    ):
        SQLiteEventStore(
            "events.db",
            busy_timeout_seconds=-1,
        )


async def test_sqlite_event_store_requires_timezone_aware_retention_timestamp(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    with pytest.raises(
        ValueError,
        match="before must be timezone-aware",
    ):
        await store.delete_before(datetime(2026, 1, 1))


class NoOffsetTimezone(tzinfo):
    """A tzinfo that makes datetimes naive: utcoffset() returns None."""

    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> str:
        return "unknown"


async def test_sqlite_event_store_rejects_retention_timestamp_without_utc_offset(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    with pytest.raises(ValueError, match="before must be timezone-aware"):
        await store.delete_before(datetime(2026, 1, 1, tzinfo=NoOffsetTimezone()))


async def test_sqlite_event_store_retention_accepts_utc_and_offset_timestamps(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")
    await store.emit(event(timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))
    await store.emit(event(timestamp=datetime(2026, 1, 1, 14, 0, tzinfo=UTC)))
    await store.emit(event(timestamp=datetime(2026, 1, 1, 16, 0, tzinfo=UTC)))

    # 15:00 at UTC+2 is 13:00 UTC, so only the 12:00 UTC event is older.
    plus_two = timezone(timedelta(hours=2))
    assert await store.delete_before(datetime(2026, 1, 1, 15, 0, tzinfo=plus_two)) == 1
    assert await store.delete_before(datetime(2026, 1, 1, 15, 0, tzinfo=UTC)) == 1
    assert len(await store.history()) == 1


async def test_sqlite_event_store_reads_database_written_by_0_11(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE relprim_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL);
        INSERT INTO relprim_schema VALUES ('event_store', 1);
        CREATE TABLE relprim_events (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            operation_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        INSERT INTO relprim_events (event_type, operation_name, timestamp, payload_json)
        VALUES (
            'retry.scheduled',
            'provider_call',
            '2026-01-01T12:00:00.000000+00:00',
            '{"attempt_number":1,"delay_seconds":0.5,"delay_source":"backoff","rate_limited":false,"retry_after_seconds":null}'
        );
        """
    )
    connection.close()

    store = SQLiteEventStore(database)
    (stored,) = await store.history()

    assert stored.sequence_id == 1
    assert stored.event.event_type is EventType.RETRY_SCHEDULED
    assert stored.event.timestamp == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert dict(stored.event.payload) == {
        "attempt_number": 1,
        "delay_seconds": 0.5,
        "delay_source": "backoff",
        "rate_limited": False,
        "retry_after_seconds": None,
    }


async def test_sqlite_event_store_round_trips_non_utc_timestamps_and_unicode(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")
    timestamp = datetime(2026, 1, 1, 14, 0, 0, 1, tzinfo=timezone(timedelta(hours=2)))
    original = event(
        operation_name="zażółć gęślą jaźń",
        timestamp=timestamp,
        payload={"reason": "błąd 🚨", "big": 2**53, "ratio": 0.1, "flag": True, "none": None},
    )

    await store.emit(original)
    (stored,) = await store.history()

    assert stored.event == original
    assert stored.event.timestamp.utcoffset() == timedelta(0)


def test_sqlite_event_stores_compare_by_identity(tmp_path: Path) -> None:
    first = SQLiteEventStore(tmp_path / "events.db")
    second = SQLiteEventStore(tmp_path / "events.db")

    assert first != second
    assert len({first, second}) == 2


async def test_sqlite_event_store_enables_wal_mode(tmp_path: Path) -> None:
    database = tmp_path / "events.db"

    await SQLiteEventStore(database).initialize()

    connection = sqlite3.connect(database)
    try:
        (journal_mode,) = connection.execute("PRAGMA journal_mode").fetchone()
    finally:
        connection.close()

    assert journal_mode == "wal"


async def test_concurrent_first_initialization_of_one_database(tmp_path: Path) -> None:
    # Several stores, workers or processes may create the same database at once.
    database = tmp_path / "events.db"
    stores = [SQLiteEventStore(database) for _ in range(8)]

    await asyncio.gather(
        *(store.emit(event(payload={"store": index})) for index, store in enumerate(stores))
    )

    history = await stores[0].history()
    assert sorted(stored.event.payload["store"] for stored in history) == list(range(8))
