from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

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
    """

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("seconds must be greater than 0.")

    async def run_async(
        self,
        operation: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Run an asynchronous operation with timeout enforcement."""
        try:
            async with asyncio.timeout(self.seconds):
                return await operation(*args, **kwargs)
        except TimeoutError as exc:
            raise OperationTimeoutError(
                f"Operation timed out after {self.seconds} second(s).",
                timeout_seconds=self.seconds,
                cause=exc,
            ) from exc
