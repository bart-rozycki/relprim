from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, ParamSpec, Protocol, TypeAlias, TypeVar, cast

from relprim._checks import check_seconds
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
    """Value returned by an idempotent operation.

    `key` is the caller's own idempotency key. RelPrim never copies it into
    execution reports or structured events.
    """

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
    """Storage and coordination interface for idempotent execution.

    Implementations must:

    - execute `operation` at most once at a time per key;
    - let concurrent callers of an in-flight key share its outcome (`joined`);
    - replay successful results until `ttl_seconds` have elapsed (`replayed`);
    - never cache failures or cancellations.
    """

    async def execute(
        self,
        key: str,
        operation: Callable[[], Awaitable[R]],
        *,
        ttl_seconds: float,
    ) -> IdempotencyResult[R]:
        """Execute or reuse an operation associated with an idempotency key."""


class _OwnerCancelled(Exception):
    """Delivered to joined callers when the owning caller was cancelled."""


@dataclass(slots=True)
class _Entry:
    future: asyncio.Future[object]
    owner_task: asyncio.Task[object] | None
    expires_at: float | None = None


@dataclass(slots=True, eq=False)
class InMemoryIdempotencyStore:
    """Single-process idempotency store for asyncio applications.

    The store coordinates concurrent calls inside one Python process and one
    event loop.

    Successful results are retained until their TTL expires: an entry whose
    expiry time is less than or equal to the store clock is expired. Expired
    entries are removed lazily on the next execute(), purge_expired() or
    entry_count() call; the store runs no background cleanup.

    Failures are not cached: the failure is delivered to the owner and to every
    joined caller, and the key is released so a later call executes again.

    If the owning caller is cancelled, the key is released and joined callers
    compete for it again: one becomes the new owner and executes the
    operation, the others join it. Cancelling a joined caller never cancels
    the shared execution.

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

        while True:
            async with self._lock:
                self._purge_expired_locked(self.clock())

                entry = self._entries.get(validated_key)

                if entry is None:
                    future = asyncio.get_running_loop().create_future()
                    self._entries[validated_key] = _Entry(
                        future=future,
                        owner_task=current_task,
                    )
                    break

                if not entry.future.done() and entry.owner_task is current_task:
                    raise IdempotencyReentryError(validated_key)

                joined_future = entry.future

            status = (
                IdempotencyStatus.REPLAYED if joined_future.done() else IdempotencyStatus.JOINED
            )

            try:
                value = cast(R, await asyncio.shield(joined_future))
            except _OwnerCancelled:
                continue

            return IdempotencyResult(
                value=value,
                key=validated_key,
                status=status,
            )

        try:
            value = await operation()
        except asyncio.CancelledError:
            # Release the key synchronously so it is never left occupied, even
            # if this task is cancelled again while cleaning up.
            self._remove_entry_now(validated_key, future)

            if not future.done():
                future.set_exception(_OwnerCancelled())
                future.exception()

            raise
        except Exception as exc:
            self._remove_entry_now(validated_key, future)

            if not future.done():
                future.set_exception(exc)

                # The owner raises the original exception directly. Retrieving it
                # here prevents an unobserved Future exception warning when no
                # other caller joined the execution.
                future.exception()

            raise

        # Publish the result synchronously: there is no await between the
        # operation completing and joined callers being released.
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

    def _remove_entry_now(
        self,
        key: str,
        future: asyncio.Future[object],
    ) -> None:
        # No critical section awaits while holding the lock, so this
        # synchronous removal cannot interleave with one.
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
    """Derives an idempotency key and coordinates operation execution.

    `key_factory` receives the same arguments as the operation and must return
    a non-blank string. Exceptions raised by the key factory propagate
    unchanged.
    """

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
    check_seconds(ttl_seconds, "ttl_seconds", allow_zero=False)
