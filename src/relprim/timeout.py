from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

from relprim._checks import check_seconds
from relprim.errors import OperationTimeoutError

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    """Timeout policy for asynchronous operations.

    RelPrim intentionally does not provide generic hard timeouts for arbitrary
    synchronous functions. Python cannot safely interrupt blocking sync code
    without additional execution boundaries such as subprocesses, workers,
    provider-native timeouts or cooperative cancellation.

    For synchronous integrations, configure timeouts at the client/provider
    level and let RelPrim retry the timeout exceptions raised by that client.

    Only expiry of this policy's own deadline raises OperationTimeoutError. A
    TimeoutError raised by the operation itself, such as a client-side socket
    timeout, propagates unchanged. Cancellation of the surrounding task
    propagates as asyncio.CancelledError.
    """

    seconds: float

    def __post_init__(self) -> None:
        check_seconds(self.seconds, "seconds", allow_zero=False)

    async def run_async(
        self,
        operation: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Run an asynchronous operation with timeout enforcement."""
        deadline = asyncio.timeout(self.seconds)

        try:
            async with deadline:
                return await operation(*args, **kwargs)
        except TimeoutError as exc:
            if not deadline.expired():
                raise

            raise OperationTimeoutError(
                f"Operation timed out after {self.seconds} second(s).",
                timeout_seconds=self.seconds,
                cause=exc,
            ) from exc
