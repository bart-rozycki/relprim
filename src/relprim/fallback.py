from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, ParamSpec, TypeVar

from relprim.errors import FallbackChainError

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class FallbackCandidate(Generic[P, R]):
    """Single fallback candidate.

    A candidate wraps an async callable with a stable name. The name is used for
    debugging, reporting and future structured events.
    """

    name: str
    operation: Callable[P, Awaitable[R]]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    """Fallback decision policy.

    `fallback_on` defines which exceptions should trigger a fallback.

    Non-matching exceptions are propagated immediately. This prevents RelPrim
    from accidentally hiding programmer errors, validation failures or other
    failures that should not be routed to another provider.
    """

    fallback_on: tuple[type[BaseException], ...] = (Exception,)

    def __post_init__(self) -> None:
        if not self.fallback_on:
            raise ValueError("fallback_on must contain at least one exception type.")

    def should_fallback(self, exception: BaseException) -> bool:
        return isinstance(exception, self.fallback_on)


@dataclass(frozen=True, slots=True)
class FallbackResult(Generic[R]):
    """Result returned by a successful fallback chain execution."""

    value: R
    candidate_name: str
    candidate_index: int
    fallback_used: bool
    failures: tuple[BaseException, ...]

    @property
    def failure_count(self) -> int:
        return len(self.failures)


@dataclass(frozen=True, slots=True)
class FallbackChain(Generic[P, R]):
    """Async fallback chain for external operations.

    The chain tries candidates in order and returns the first successful result.

    It intentionally does not implement retries, timeouts, circuit breakers or
    execution reports. Those are composed at a higher level by RelPrim operation
    APIs.
    """

    candidates: tuple[FallbackCandidate[P, R], ...]
    policy: FallbackPolicy = FallbackPolicy()

    def __post_init__(self) -> None:
        if not self.candidates:
            raise ValueError("candidates must contain at least one fallback candidate.")

    @classmethod
    def from_operations(
        cls,
        *operations: tuple[str, Callable[P, Awaitable[R]]],
        policy: FallbackPolicy | None = None,
    ) -> FallbackChain[P, R]:
        """Create a fallback chain from named async operations."""
        candidates = tuple(
            FallbackCandidate(name=name, operation=operation) for name, operation in operations
        )

        return cls(
            candidates=candidates,
            policy=policy or FallbackPolicy(),
        )

    async def run(self, *args: P.args, **kwargs: P.kwargs) -> FallbackResult[R]:
        """Run candidates in order and return the first successful result."""
        failures: list[BaseException] = []

        for candidate_index, candidate in enumerate(self.candidates):
            try:
                value = await candidate.operation(*args, **kwargs)
            except Exception as exc:
                if not self.policy.should_fallback(exc):
                    raise

                failures.append(exc)
                continue

            return FallbackResult(
                value=value,
                candidate_name=candidate.name,
                candidate_index=candidate_index,
                fallback_used=candidate_index > 0,
                failures=tuple(failures),
            )

        raise FallbackChainError(
            f"All {len(self.candidates)} fallback candidate(s) failed.",
            failures=tuple(failures),
        )


def fallback_chain(
    *operations: tuple[str, Callable[P, Awaitable[R]]],
    policy: FallbackPolicy | None = None,
) -> FallbackChain[P, R]:
    """Create an async fallback chain from named operations."""
    return FallbackChain.from_operations(*operations, policy=policy)
