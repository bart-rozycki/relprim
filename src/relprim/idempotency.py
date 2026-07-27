from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, ParamSpec, Protocol, TypeAlias, TypeVar, cast

from relprim.errors import IdempotencyReentryError

P = ParamSpec("P")
R = TypeVar("R")

Clock: TypeAlias = Callable[[], float]


class IdempotencyStatus(StrEnum):
    """Describes how an idempotent result was obtained."""

    EXECUTED = "executed"
    JOINED = "joined"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class IdempotencyResult(Generic[R]):
    """Value returned by an idempotent operation."""

    value: R
    key: str
    status: IdempotencyStatus

    def __post_init__(self) -> None:
        _validate_key(self.key)

    @property
    def executed(self) -> bool:
        return self.status is IdempotencyStatus.EXECUTED

    @property
    def joined(self) -> bool:
        return self.status is IdempotencyStatus.JOINED

    @property
    def replayed(self) -> bool:
        return self.status is IdempotencyStatus.REPLAYED

    @property
    def cache_hit(self) -> bool:
        return self.status in {
            IdempotencyStatus.JOINED,
            IdempotencyStatus.REPLAYED,
        }


class IdempotencyStore(Protocol):
    """Storage and coordination interface for idempotent execution."""

    async def execute(
        self,
        key: str,
        operation: Callable[[], Awaitable[R]],
        *,
        ttl_seconds: float,
    ) -> IdempotencyResult[R]:
        """Execute or reuse an operation associated with an idempotency key."""


@dataclass(slots=True)
class _Entry:
    future: asyncio.Future[object]
    owner_task: asyncio.Task[object] | None
    expires_at: float | None = None


@dataclass(slots=True)
class InMemoryIdempotencyStore:
    """Single-process idempotency store for asyncio applications.

    The store coordinates concurrent calls inside one Python process.

    Successful results are retained until their TTL expires. Failures are not
    cached, allowing later calls to retry the operation.

    This store is suitable for tests, local applications and single-process
    services. Distributed applications should use a persistent shared store.
    """

    clock: Clock = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def execute(
        self,
        key: str,
        operation: Callable[[], Awaitable[R]],
        *,
        ttl_seconds: float,
    ) -> IdempotencyResult[R]:
        validated_key = _validate_key(key)
        _validate_ttl(ttl_seconds)

        current_task = cast(asyncio.Task[object] | None, asyncio.current_task())

        async with self._lock:
            self._purge_expired_locked(self.clock())

            entry = self._entries.get(validated_key)

            if entry is not None:
                if not entry.future.done() and entry.owner_task is current_task:
                    raise IdempotencyReentryError(validated_key)

                future = entry.future
                status = IdempotencyStatus.REPLAYED if future.done() else IdempotencyStatus.JOINED
                owns_execution = False
            else:
                future = asyncio.get_running_loop().create_future()
                entry = _Entry(
                    future=future,
                    owner_task=current_task,
                )
                self._entries[validated_key] = entry
                status = IdempotencyStatus.EXECUTED
                owns_execution = True

        if not owns_execution:
            value = cast(R, await asyncio.shield(future))

            return IdempotencyResult(
                value=value,
                key=validated_key,
                status=status,
            )

        try:
            value = await operation()
        except asyncio.CancelledError:
            await self._remove_entry(validated_key, future)

            if not future.done():
                future.cancel()

            raise
        except Exception as exc:
            await self._remove_entry(validated_key, future)

            if not future.done():
                future.set_exception(exc)

                # The owner raises the original exception directly. Retrieving it
                # here prevents an unobserved Future exception warning when no
                # other caller joined the execution.
                future.exception()

            raise

        async with self._lock:
            current_entry = self._entries.get(validated_key)

            if current_entry is not None and current_entry.future is future:
                current_entry.expires_at = self.clock() + ttl_seconds

            if not future.done():
                future.set_result(value)

        return IdempotencyResult(
            value=value,
            key=validated_key,
            status=IdempotencyStatus.EXECUTED,
        )

    async def purge_expired(self) -> int:
        """Remove expired completed entries and return the number removed."""
        async with self._lock:
            return self._purge_expired_locked(self.clock())

    async def entry_count(self) -> int:
        """Return the current number of in-flight and cached entries."""
        async with self._lock:
            self._purge_expired_locked(self.clock())
            return len(self._entries)

    async def _remove_entry(
        self,
        key: str,
        future: asyncio.Future[object],
    ) -> None:
        async with self._lock:
            entry = self._entries.get(key)

            if entry is not None and entry.future is future:
                del self._entries[key]

    def _purge_expired_locked(self, now: float) -> int:
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if entry.expires_at is not None and entry.expires_at <= now
        ]

        for key in expired_keys:
            del self._entries[key]

        return len(expired_keys)


@dataclass(frozen=True, slots=True)
class IdempotencyPolicy(Generic[P]):
    """Derives an idempotency key and coordinates operation execution."""

    key_factory: Callable[P, str]
    store: IdempotencyStore = field(default_factory=InMemoryIdempotencyStore)
    ttl_seconds: float = 3600.0

    def __post_init__(self) -> None:
        _validate_ttl(self.ttl_seconds)

    async def run(
        self,
        operation: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> IdempotencyResult[R]:
        key = _validate_key(self.key_factory(*args, **kwargs))

        async def execute_operation() -> R:
            return await operation(*args, **kwargs)

        return await self.store.execute(
            key,
            execute_operation,
            ttl_seconds=self.ttl_seconds,
        )


def idempotency_policy(
    key_factory: Callable[P, str],
    *,
    store: IdempotencyStore | None = None,
    ttl_seconds: float = 3600.0,
) -> IdempotencyPolicy[P]:
    """Create an idempotency policy."""

    resolved_store = store if store is not None else InMemoryIdempotencyStore()

    return IdempotencyPolicy(
        key_factory=key_factory,
        store=resolved_store,
        ttl_seconds=ttl_seconds,
    )


def _validate_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError("idempotency key must be a string.")

    if not key.strip():
        raise ValueError("idempotency key must not be empty.")

    return key


def _validate_ttl(ttl_seconds: float) -> None:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int | float):
        raise TypeError("ttl_seconds must be an int or float.")

    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be greater than 0.")
