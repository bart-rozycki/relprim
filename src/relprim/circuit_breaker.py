from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ParamSpec, TypeAlias, TypeVar

from relprim.errors import CircuitBreakerOpenError

P = ParamSpec("P")
R = TypeVar("R")

Clock: TypeAlias = Callable[[], float]


class CircuitBreakerState(StrEnum):
    """Circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerSnapshot:
    """Immutable snapshot of circuit breaker state."""

    name: str
    state: CircuitBreakerState
    failure_count: int
    failure_threshold: int
    opened_at: float | None
    recovery_timeout_seconds: float
    half_open_probe_in_flight: bool

    @property
    def open(self) -> bool:
        return self.state is CircuitBreakerState.OPEN

    @property
    def closed(self) -> bool:
        return self.state is CircuitBreakerState.CLOSED

    @property
    def half_open(self) -> bool:
        return self.state is CircuitBreakerState.HALF_OPEN


@dataclass(slots=True)
class CircuitBreaker:
    """Async circuit breaker for protecting unstable external operations.

    The circuit breaker protects downstream systems by temporarily rejecting
    calls after repeated failures.

    State transitions:

    - CLOSED: calls are allowed and failures are counted.
    - OPEN: calls are rejected immediately.
    - HALF_OPEN: after the recovery timeout, a single probe call is allowed.
      If it succeeds, the breaker closes. If it fails, the breaker opens again.

    This primitive is intentionally async-first and uses an asyncio lock to
    prevent concurrent half-open probes from stampeding a recovering dependency.
    """

    name: str = "default"
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    record_failure_on: tuple[type[BaseException], ...] = (Exception,)
    clock: Clock = time.monotonic

    _state: CircuitBreakerState = field(
        default=CircuitBreakerState.CLOSED,
        init=False,
        repr=False,
    )
    _failure_count: int = field(default=0, init=False, repr=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)
    _half_open_probe_in_flight: bool = field(default=False, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be greater than or equal to 1.")
        if self.recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be greater than 0.")
        if not self.record_failure_on:
            raise ValueError("record_failure_on must contain at least one exception type.")

    async def run_async(
        self,
        operation: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Run an async operation through the circuit breaker."""
        await self._before_call()

        try:
            result = await operation(*args, **kwargs)
        except Exception as exc:
            await self._after_failure(exc)
            raise

        await self._after_success()
        return result

    async def snapshot(self) -> CircuitBreakerSnapshot:
        """Return a consistent snapshot of current breaker state."""
        async with self._lock:
            return CircuitBreakerSnapshot(
                name=self.name,
                state=self._state,
                failure_count=self._failure_count,
                failure_threshold=self.failure_threshold,
                opened_at=self._opened_at,
                recovery_timeout_seconds=self.recovery_timeout_seconds,
                half_open_probe_in_flight=self._half_open_probe_in_flight,
            )

    async def reset(self) -> None:
        """Reset the breaker to the closed state."""
        async with self._lock:
            self._close()

    async def _before_call(self) -> None:
        async with self._lock:
            now = self.clock()

            if self._state is CircuitBreakerState.OPEN:
                if self._opened_at is not None:
                    elapsed = now - self._opened_at

                    if elapsed >= self.recovery_timeout_seconds:
                        self._state = CircuitBreakerState.HALF_OPEN
                        self._half_open_probe_in_flight = True
                        return

                    retry_after_seconds = max(0.0, self.recovery_timeout_seconds - elapsed)
                else:
                    retry_after_seconds = self.recovery_timeout_seconds

                raise self._open_error(retry_after_seconds=retry_after_seconds)

            if self._state is CircuitBreakerState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    raise self._open_error(retry_after_seconds=None)

                self._half_open_probe_in_flight = True
                return

    async def _after_success(self) -> None:
        async with self._lock:
            if self._state is CircuitBreakerState.HALF_OPEN:
                self._close()
                return

            if self._state is CircuitBreakerState.CLOSED:
                self._failure_count = 0

    async def _after_failure(self, exception: Exception) -> None:
        async with self._lock:
            if not isinstance(exception, self.record_failure_on):
                if self._state is CircuitBreakerState.HALF_OPEN:
                    self._half_open_probe_in_flight = False

                return

            if self._state is CircuitBreakerState.HALF_OPEN:
                self._open()
                return

            if self._state is CircuitBreakerState.CLOSED:
                self._failure_count += 1

                if self._failure_count >= self.failure_threshold:
                    self._open()

    def _open(self) -> None:
        self._state = CircuitBreakerState.OPEN
        self._opened_at = self.clock()
        self._half_open_probe_in_flight = False

    def _close(self) -> None:
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._opened_at = None
        self._half_open_probe_in_flight = False

    def _open_error(self, *, retry_after_seconds: float | None) -> CircuitBreakerOpenError:
        return CircuitBreakerOpenError(
            f"Circuit breaker '{self.name}' is {self._state.value}.",
            breaker_name=self.name,
            state=self._state.value,
            retry_after_seconds=retry_after_seconds,
        )
