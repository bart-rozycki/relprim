from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, ParamSpec, TypeVar

from relprim._checks import check_exception_types
from relprim.errors import FallbackChainError

P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)


@dataclass(frozen=True, slots=True)
class FallbackCandidate(Generic[P, R_co]):
    """Single fallback candidate.

    A candidate wraps an async callable with a stable name. The name is used in
    execution reports and structured events.
    """

    name: str
    operation: Callable[P, Awaitable[R_co]]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    """Fallback decision policy.

    `fallback_on` defines which exceptions should trigger a fallback. It applies
    both to the failure of the primary operation (inside AsyncOperation) and to
    the failure of each candidate before the next one is tried.

    Non-matching exceptions are propagated immediately. This prevents RelPrim
    from accidentally hiding programmer errors, validation failures or other
    failures that should not be routed to another provider.

    `fallback_on` must contain Exception subclasses; cancellation never
    triggers a fallback.
    """

    fallback_on: tuple[type[Exception], ...] = (Exception,)

    def __post_init__(self) -> None:
        check_exception_types(self.fallback_on, "fallback_on")

    def should_fallback(self, exception: BaseException) -> bool:
        return isinstance(exception, self.fallback_on)


@dataclass(frozen=True, slots=True)
class FallbackResult(Generic[R_co]):
    """Result returned by a successful fallback chain execution.

    `failures` contains the exceptions of the candidates that failed before the
    successful one, in candidate order.
    """

    value: R_co
    candidate_name: str
    candidate_index: int
    fallback_used: bool
    failures: tuple[Exception, ...]

    @property
    def failure_count(self) -> int:
        return len(self.failures)


@dataclass(frozen=True, slots=True)
class FallbackChain(Generic[P, R_co]):
    """Async fallback chain for external operations.

    The chain tries candidates in order and returns the first successful result.

    It intentionally does not implement retries, timeouts, circuit breakers or
    execution reports. Those are composed at a higher level by RelPrim operation
    APIs.
    """

    candidates: tuple[FallbackCandidate[P, R_co], ...]
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

        return FallbackChain(
            candidates=candidates,
            policy=policy or FallbackPolicy(),
        )

    async def run(self, *args: P.args, **kwargs: P.kwargs) -> FallbackResult[R_co]:
        """Run candidates in order and return the first successful result."""
        failures: list[Exception] = []

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
