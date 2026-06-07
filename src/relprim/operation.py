from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, ParamSpec, TypeAlias, TypeVar

from relprim.errors import FallbackChainError, OperationExecutionError, OperationTimeoutError
from relprim.fallback import FallbackChain, FallbackResult
from relprim.report import (
    AttemptStatus,
    ExecutionAttempt,
    ExecutionError,
    ExecutionReport,
    ExecutionStatus,
)
from relprim.result import OperationResult
from relprim.retry import RetryPolicy
from relprim.timeout import TimeoutPolicy

P = ParamSpec("P")
R = TypeVar("R")

MetadataValue: TypeAlias = str | int | float | bool | None
Metadata: TypeAlias = Mapping[str, MetadataValue]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_seconds(started_at: float) -> float:
    return time.perf_counter() - started_at


@dataclass(frozen=True, slots=True)
class AsyncOperation(Generic[P, R]):
    """Composable resilient execution wrapper for asynchronous operations.

    AsyncOperation is the first higher-level RelPrim API. It composes low-level
    primitives such as retry, timeout and fallback policies, while returning an
    OperationResult that contains both the business value and an execution report.

    This class intentionally does not know anything about AI providers, HTTP,
    payments, queues or storage. It operates on any async callable.
    """

    name: str
    _operation: Callable[P, Awaitable[R]]
    _retry_policy: RetryPolicy | None = None
    _timeout_policy: TimeoutPolicy | None = None
    _fallback_chain: FallbackChain[P, R] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")

    def with_retry(self, policy: RetryPolicy) -> AsyncOperation[P, R]:
        """Return a new operation configured with a retry policy."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=policy,
            _timeout_policy=self._timeout_policy,
            _fallback_chain=self._fallback_chain,
        )

    def with_timeout(self, policy: TimeoutPolicy) -> AsyncOperation[P, R]:
        """Return a new operation configured with a timeout policy."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=policy,
            _fallback_chain=self._fallback_chain,
        )

    def with_fallbacks(self, chain: FallbackChain[P, R]) -> AsyncOperation[P, R]:
        """Return a new operation configured with fallback candidates."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=self._timeout_policy,
            _fallback_chain=chain,
        )

    async def run(self, *args: P.args, **kwargs: P.kwargs) -> OperationResult[R]:
        """Run the operation and return its value with an execution report.

        Success returns OperationResult[T].

        Failure raises OperationExecutionError with the final ExecutionReport
        attached. This keeps the default failure mode safe: callers cannot
        accidentally ignore failed operations by forgetting to inspect a status.
        """
        operation_started_at = _utc_now()
        operation_started_monotonic = time.perf_counter()
        attempts: list[ExecutionAttempt] = []

        max_attempts = self._retry_policy.max_attempts if self._retry_policy is not None else 1

        for attempt_number in range(1, max_attempts + 1):
            attempt_started_at = _utc_now()
            attempt_started_monotonic = time.perf_counter()

            try:
                value = await self._run_single_attempt(*args, **kwargs)
            except Exception as exc:
                attempt_status = self._attempt_status_for_exception(exc)
                retryable = self._is_retryable(exc)

                attempts.append(
                    ExecutionAttempt(
                        attempt_number=attempt_number,
                        status=attempt_status,
                        started_at=attempt_started_at,
                        duration_seconds=_elapsed_seconds(attempt_started_monotonic),
                        error=ExecutionError.from_exception(exc, retryable=retryable),
                        metadata={"fallback_used": False},
                    )
                )

                if not retryable or attempt_number >= max_attempts:
                    if self._fallback_chain is not None:
                        fallback_attempt_number = len(attempts) + 1
                        fallback_started_at = _utc_now()
                        fallback_started_monotonic = time.perf_counter()

                        try:
                            fallback_result = await self._fallback_chain.run(*args, **kwargs)
                        except Exception as fallback_exc:
                            metadata = self._fallback_failure_metadata(fallback_exc)

                            attempts.append(
                                ExecutionAttempt(
                                    attempt_number=fallback_attempt_number,
                                    status=self._attempt_status_for_exception(fallback_exc),
                                    started_at=fallback_started_at,
                                    duration_seconds=_elapsed_seconds(fallback_started_monotonic),
                                    error=ExecutionError.from_exception(
                                        fallback_exc, retryable=False
                                    ),
                                    metadata=metadata,
                                )
                            )

                            report = self._build_report(
                                status=self._report_status_for_attempt_status(attempts[-1].status),
                                started_at=operation_started_at,
                                started_monotonic=operation_started_monotonic,
                                attempts=attempts,
                                metadata=metadata,
                            )

                            raise OperationExecutionError(
                                f"Operation '{self.name}' failed and fallback chain failed.",
                                report=report,
                                cause=fallback_exc,
                            ) from fallback_exc

                        metadata = self._fallback_success_metadata(fallback_result)

                        attempts.append(
                            ExecutionAttempt(
                                attempt_number=fallback_attempt_number,
                                status=AttemptStatus.SUCCEEDED,
                                started_at=fallback_started_at,
                                duration_seconds=_elapsed_seconds(fallback_started_monotonic),
                                metadata=metadata,
                            )
                        )

                        report = self._build_report(
                            status=ExecutionStatus.SUCCEEDED,
                            started_at=operation_started_at,
                            started_monotonic=operation_started_monotonic,
                            attempts=attempts,
                            metadata=metadata,
                        )

                        return OperationResult(value=fallback_result.value, report=report)

                    report = self._build_report(
                        status=self._report_status_for_attempt_status(attempt_status),
                        started_at=operation_started_at,
                        started_monotonic=operation_started_monotonic,
                        attempts=attempts,
                        metadata={"fallback_used": False},
                    )

                    raise OperationExecutionError(
                        f"Operation '{self.name}' failed after {len(attempts)} attempt(s).",
                        report=report,
                        cause=exc,
                    ) from exc

                await self._sleep_before_retry(attempt_number)
                continue

            attempts.append(
                ExecutionAttempt(
                    attempt_number=attempt_number,
                    status=AttemptStatus.SUCCEEDED,
                    started_at=attempt_started_at,
                    duration_seconds=_elapsed_seconds(attempt_started_monotonic),
                    metadata={"fallback_used": False},
                )
            )

            report = self._build_report(
                status=ExecutionStatus.SUCCEEDED,
                started_at=operation_started_at,
                started_monotonic=operation_started_monotonic,
                attempts=attempts,
                metadata={"fallback_used": False},
            )

            return OperationResult(value=value, report=report)

        raise RuntimeError("AsyncOperation reached an invalid execution state.")

    async def _run_single_attempt(self, *args: P.args, **kwargs: P.kwargs) -> R:
        if self._timeout_policy is None:
            return await self._operation(*args, **kwargs)

        return await self._timeout_policy.run_async(self._operation, *args, **kwargs)

    async def _sleep_before_retry(self, attempt_number: int) -> None:
        if self._retry_policy is None:
            raise RuntimeError("Retry sleep requested without retry policy.")

        delay = self._retry_policy.backoff.delay_for_retry(attempt_number)
        await self._retry_policy.async_sleeper(delay)

    def _is_retryable(self, exception: Exception) -> bool:
        if self._retry_policy is None:
            return False

        return isinstance(exception, self._retry_policy.retry_on)

    @staticmethod
    def _attempt_status_for_exception(exception: Exception) -> AttemptStatus:
        if isinstance(exception, OperationTimeoutError):
            return AttemptStatus.TIMED_OUT

        return AttemptStatus.FAILED

    @staticmethod
    def _report_status_for_attempt_status(status: AttemptStatus) -> ExecutionStatus:
        if status is AttemptStatus.TIMED_OUT:
            return ExecutionStatus.TIMED_OUT

        if status is AttemptStatus.CANCELLED:
            return ExecutionStatus.CANCELLED

        return ExecutionStatus.FAILED

    @staticmethod
    def _fallback_success_metadata(result: FallbackResult[R]) -> dict[str, MetadataValue]:
        return {
            "fallback_used": True,
            "fallback_failed": False,
            "fallback_candidate_name": result.candidate_name,
            "fallback_candidate_index": result.candidate_index,
            "fallback_failure_count": result.failure_count,
        }

    @staticmethod
    def _fallback_failure_metadata(exception: Exception) -> dict[str, MetadataValue]:
        failure_count = len(exception.failures) if isinstance(exception, FallbackChainError) else 0

        return {
            "fallback_used": True,
            "fallback_failed": True,
            "fallback_failure_count": failure_count,
        }

    def _build_report(
        self,
        *,
        status: ExecutionStatus,
        started_at: datetime,
        started_monotonic: float,
        attempts: list[ExecutionAttempt],
        metadata: Metadata | None = None,
    ) -> ExecutionReport:
        return ExecutionReport(
            operation_name=self.name,
            status=status,
            started_at=started_at,
            duration_seconds=_elapsed_seconds(started_monotonic),
            attempts=tuple(attempts),
            metadata=metadata or {},
        )


def async_operation(
    name: str,
    operation: Callable[P, Awaitable[R]],
) -> AsyncOperation[P, R]:
    """Create a resilient wrapper for an asynchronous operation."""
    return AsyncOperation(name=name, _operation=operation)
