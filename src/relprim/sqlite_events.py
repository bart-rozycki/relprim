from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from relprim.errors import SQLiteEventStoreSchemaError
from relprim.events import (
    EventPayload,
    EventType,
    JsonScalar,
    StructuredEvent,
)

SQLiteParameter: TypeAlias = str | int
Migration: TypeAlias = Callable[[sqlite3.Connection], None]

_SCHEMA_COMPONENT = "event_store"
_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """Structured event persisted by SQLiteEventStore."""

    sequence_id: int
    event: StructuredEvent

    def __post_init__(self) -> None:
        if self.sequence_id <= 0:
            raise ValueError("sequence_id must be greater than 0.")


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS relprim_events (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            operation_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relprim_events_operation_name_sequence
        ON relprim_events (operation_name, sequence_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relprim_events_event_type_sequence
        ON relprim_events (event_type, sequence_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relprim_events_timestamp
        ON relprim_events (timestamp)
        """
    )


_MIGRATIONS: dict[int, Migration] = {
    1: _migration_1,
}


@dataclass(slots=True)
class SQLiteEventStore:
    """Durable SQLite event sink with basic history queries.

    SQLite work is executed through asyncio.to_thread() so database I/O does
    not block the asyncio event loop.

    Each database operation creates and closes its own sqlite3 connection.
    The store does not keep a connection or background worker alive.

    The store is intended for local and single-host persistence. It is not a
    distributed event store.
    """

    database: str | Path
    busy_timeout_seconds: float = 5.0

    _database_path: Path = field(init=False, repr=False)
    _initialized: bool = field(
        default=False,
        init=False,
        repr=False,
    )
    _initialize_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        database_value = str(self.database)

        if not database_value.strip():
            raise ValueError("database must not be empty.")

        if database_value == ":memory:":
            raise ValueError("SQLiteEventStore does not support ':memory:' databases.")

        if isinstance(self.busy_timeout_seconds, bool) or not isinstance(
            self.busy_timeout_seconds, int | float
        ):
            raise TypeError("busy_timeout_seconds must be an int or float.")

        if not math.isfinite(float(self.busy_timeout_seconds)):
            raise ValueError("busy_timeout_seconds must be finite.")

        if self.busy_timeout_seconds < 0:
            raise ValueError("busy_timeout_seconds must be greater than or equal to 0.")

        self._database_path = Path(database_value)

    async def initialize(self) -> None:
        """Create or migrate the event-store schema."""
        await self._ensure_initialized()

    async def emit(self, event: StructuredEvent) -> None:
        """Persist a structured event."""
        await self._ensure_initialized()

        await asyncio.to_thread(
            self._emit_sync,
            event,
        )

    async def history(
        self,
        *,
        operation_name: str | None = None,
        event_types: tuple[EventType, ...] | None = None,
        after_sequence_id: int | None = None,
        limit: int = 100,
    ) -> tuple[StoredEvent, ...]:
        """Return stored events ordered by their SQLite sequence id."""

        if operation_name is not None and not operation_name.strip():
            raise ValueError("operation_name must not be empty when provided.")

        if event_types is not None:
            for event_type in event_types:
                if not isinstance(event_type, EventType):
                    raise TypeError("event_types must contain EventType values.")

            if not event_types:
                return ()

        if after_sequence_id is not None:
            if isinstance(after_sequence_id, bool) or not isinstance(after_sequence_id, int):
                raise TypeError("after_sequence_id must be an int.")

            if after_sequence_id < 0:
                raise ValueError("after_sequence_id must be greater than or equal to 0.")

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an int.")

        if limit <= 0:
            raise ValueError("limit must be greater than 0.")

        await self._ensure_initialized()

        return await asyncio.to_thread(
            self._history_sync,
            operation_name,
            event_types,
            after_sequence_id,
            limit,
        )

    async def delete_before(
        self,
        before: datetime,
    ) -> int:
        """Delete events older than the supplied timestamp."""

        if before.tzinfo is None:
            raise ValueError("before must be timezone-aware.")

        await self._ensure_initialized()

        return await asyncio.to_thread(
            self._delete_before_sync,
            before,
        )

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._initialize_lock:
            if self._initialized:
                return

            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=float(self.busy_timeout_seconds),
        )

        connection.row_factory = sqlite3.Row

        busy_timeout_ms = int(float(self.busy_timeout_seconds) * 1000)

        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")

        return connection

    def _initialize_sync(self) -> None:
        connection = self._connect()

        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS relprim_schema (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                )
                """
            )

            current_version = self._read_schema_version(connection)

            if current_version > _SCHEMA_VERSION:
                raise SQLiteEventStoreSchemaError(
                    "SQLite event-store schema version "
                    f"{current_version} is newer than the supported "
                    f"version {_SCHEMA_VERSION}."
                )

            for target_version in range(
                current_version + 1,
                _SCHEMA_VERSION + 1,
            ):
                migration = _MIGRATIONS.get(target_version)

                if migration is None:
                    raise SQLiteEventStoreSchemaError(
                        "No SQLite event-store migration exists for "
                        f"schema version {target_version}."
                    )

                migration(connection)

                connection.execute(
                    """
                    INSERT OR REPLACE INTO relprim_schema (
                        component,
                        version
                    )
                    VALUES (?, ?)
                    """,
                    (
                        _SCHEMA_COMPONENT,
                        target_version,
                    ),
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _read_schema_version(
        connection: sqlite3.Connection,
    ) -> int:
        row = connection.execute(
            """
            SELECT version
            FROM relprim_schema
            WHERE component = ?
            """,
            (_SCHEMA_COMPONENT,),
        ).fetchone()

        if row is None:
            return 0

        version = row["version"]

        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise SQLiteEventStoreSchemaError("SQLite event-store schema version is invalid.")

        return version

    def _emit_sync(
        self,
        event: StructuredEvent,
    ) -> None:
        payload_json = _encode_payload(event.payload)
        timestamp = _serialize_timestamp(event.timestamp)

        connection = self._connect()

        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO relprim_events (
                        event_type,
                        operation_name,
                        timestamp,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        event.event_type.value,
                        event.operation_name,
                        timestamp,
                        payload_json,
                    ),
                )
        finally:
            connection.close()

    def _history_sync(
        self,
        operation_name: str | None,
        event_types: tuple[EventType, ...] | None,
        after_sequence_id: int | None,
        limit: int,
    ) -> tuple[StoredEvent, ...]:
        clauses: list[str] = []
        parameters: list[SQLiteParameter] = []

        if operation_name is not None:
            clauses.append("operation_name = ?")
            parameters.append(operation_name)

        if event_types is not None:
            placeholders = ", ".join("?" for _ in event_types)

            clauses.append(f"event_type IN ({placeholders})")

            parameters.extend(event_type.value for event_type in event_types)

        if after_sequence_id is not None:
            clauses.append("sequence_id > ?")
            parameters.append(after_sequence_id)

        query = """
            SELECT
                sequence_id,
                event_type,
                operation_name,
                timestamp,
                payload_json
            FROM relprim_events
        """

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY sequence_id ASC LIMIT ?"
        parameters.append(limit)

        connection = self._connect()

        try:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()
        finally:
            connection.close()

        return tuple(_stored_event_from_row(row) for row in rows)

    def _delete_before_sync(
        self,
        before: datetime,
    ) -> int:
        serialized_before = _serialize_timestamp(before)

        connection = self._connect()

        try:
            with connection:
                cursor = connection.execute(
                    """
                    DELETE FROM relprim_events
                    WHERE timestamp < ?
                    """,
                    (serialized_before,),
                )

                return int(cursor.rowcount)
        finally:
            connection.close()


def _serialize_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware.")

    return timestamp.astimezone(UTC).isoformat(timespec="microseconds")


def _encode_payload(payload: EventPayload) -> str:
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "event payload must contain JSON-compatible finite scalar values."
        ) from exc


def _decode_payload(payload_json: str) -> dict[str, JsonScalar]:
    decoded: object = json.loads(payload_json)

    if not isinstance(decoded, dict):
        raise ValueError("stored event payload must be a JSON object.")

    payload: dict[str, JsonScalar] = {}

    for key, value in decoded.items():
        if not isinstance(key, str):
            raise ValueError("stored event payload keys must be strings.")

        if value is None:
            payload[key] = None
            continue

        if isinstance(value, bool | str | int):
            payload[key] = value
            continue

        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("stored event payload floats must be finite.")

            payload[key] = value
            continue

        raise ValueError("stored event payload values must be JSON scalar values.")

    return payload


def _stored_event_from_row(
    row: sqlite3.Row,
) -> StoredEvent:
    sequence_id = int(row["sequence_id"])
    event_type = EventType(str(row["event_type"]))
    operation_name = str(row["operation_name"])
    timestamp = datetime.fromisoformat(str(row["timestamp"]))
    payload = _decode_payload(str(row["payload_json"]))

    return StoredEvent(
        sequence_id=sequence_id,
        event=StructuredEvent(
            event_type=event_type,
            operation_name=operation_name,
            timestamp=timestamp,
            payload=payload,
        ),
    )


__all__ = [
    "SQLiteEventStore",
    "StoredEvent",
]
