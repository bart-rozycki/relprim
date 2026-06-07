from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

from relprim.errors import RetryError

P = ParamSpec("P")
R = TypeVar("R")

SyncSleeper = Callable[[float], None]
AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryAttempt:
    """Information about a single retry attempt."""

    attempt_number: int
    max_attempts: int
    exception: BaseException
    delay_seconds: float


@dataclass(frozen=True, slots=True)
class ExponentialBackoff:
    """Exponential backoff policy with optional jitter.

    The first retry waits for `base_delay_seconds`.
    Every subsequent retry multiplies the delay by `multiplier`,
    capped at `max_delay_seconds`.

    If `jitter` is enabled, a random delay between 0 and the calculated
    delay is used. This helps avoid retry storms under load.
    """

    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 10.0
    multiplier: float = 2.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be greater than or equal to 0.")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be greater than or equal to 0.")
        if self.multiplier < 1:
            raise ValueError("multiplier must be greater than or equal to 1.")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base_delay_seconds must not exceed max_delay_seconds.")

    def delay_for_retry(self, retry_number: int) -> float:
        """Return delay for a retry attempt.

        `retry_number` is 1-based:
        - retry_number=1 means the delay before the first retry
        - retry_number=2 means the delay before the second retry
        """
        if retry_number < 1:
            raise ValueError("retry_number must be greater than or equal to 1.")

        delay = self.base_delay_seconds * (self.multiplier ** (retry_number - 1))
        delay = min(delay, self.max_delay_seconds)

        if self.jitter and delay > 0:
            return random.uniform(0, delay)

        return delay


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry policy for sync and async operations.

    This primitive intentionally does not know anything about workflows,
    providers, OpenAI, HTTP clients, queues or observability.

    It only answers one question:

    "Should this operation be retried, and if yes, when?"
    """

    max_attempts: int = 3
    backoff: ExponentialBackoff = ExponentialBackoff()
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    sleeper: SyncSleeper = time.sleep
    async_sleeper: AsyncSleeper = asyncio.sleep

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be greater than or equal to 1.")
        if not self.retry_on:
            raise ValueError("retry_on must contain at least one exception type.")

    def run(
        self,
        operation: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Run a synchronous operation with retry handling."""
        last_exception: BaseException | None = None

        for attempt_number in range(1, self.max_attempts + 1):
            try:
                return operation(*args, **kwargs)
            except self.retry_on as exc:
                last_exception = exc

                if attempt_number >= self.max_attempts:
                    break

                delay = self.backoff.delay_for_retry(attempt_number)
                self.sleeper(delay)

        if last_exception is None:
            raise RuntimeError("RetryPolicy reached an invalid state without an exception.")

        raise RetryError(
            f"Operation failed after {self.max_attempts} attempt(s).",
            attempts=self.max_attempts,
            cause=last_exception,
        ) from last_exception

    async def run_async(
        self,
        operation: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Run an asynchronous operation with retry handling."""
        last_exception: BaseException | None = None

        for attempt_number in range(1, self.max_attempts + 1):
            try:
                return await operation(*args, **kwargs)
            except self.retry_on as exc:
                last_exception = exc

                if attempt_number >= self.max_attempts:
                    break

                delay = self.backoff.delay_for_retry(attempt_number)
                await self.async_sleeper(delay)

        if last_exception is None:
            raise RuntimeError("RetryPolicy reached an invalid state without an exception.")

        raise RetryError(
            f"Operation failed after {self.max_attempts} attempt(s).",
            attempts=self.max_attempts,
            cause=last_exception,
        ) from last_exception
