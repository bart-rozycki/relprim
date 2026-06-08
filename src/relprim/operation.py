from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, ParamSpec, TypeAlias, TypeVar

from relprim.circuit_breaker import CircuitBreaker
from relprim.errors import (
    CircuitBreakerOpenError,
    FallbackChainError,
    OperationExecutionError,
    OperationTimeoutError,
    ValidationFailedError,
)
from relprim.events import EventEmitter, EventType
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
from relprim.validation import ValidationPolicy, ValidationResult

P = ParamSpec("P")
R = TypeVar("R")

MetadataValue: TypeAlias = str | int | float | bool | None
Metadata: TypeAlias = Mapping[str, MetadataValue]
MutableMetadata: TypeAlias = dict[str, MetadataValue]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_seconds(started_at: float) -> float:
    return time.perf_counter() - started_at


@dataclass(frozen=True, slots=True)
class AsyncOperation(Generic[P, R]):
    """Composable resilient execution wrapper for asynchronous operations.

    AsyncOperation is the first higher-level RelPrim API. It composes low-level
    primitives such as retry, timeout, circuit breaker, fallback and validation
    policies, while returning an OperationResult that contains both the business
    value and an execution report.

    This class intentionally does not know anything about AI providers, HTTP,
    payments, queues or storage. It operates on any async callable.
    """

    name: str
    _operation: Callable[P, Awaitable[R]]
    _retry_policy: RetryPolicy | None = None
    _timeout_policy: TimeoutPolicy | None = None
    _fallback_chain: FallbackChain[P, R] | None = None
    _circuit_breaker: CircuitBreaker | None = None
    _validation_policy: ValidationPolicy[R] | None = None
    _event_emitter: EventEmitter | None = None

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
            _circuit_breaker=self._circuit_breaker,
            _validation_policy=self._validation_policy,
            _event_emitter=self._event_emitter,
        )

    def with_timeout(self, policy: TimeoutPolicy) -> AsyncOperation[P, R]:
        """Return a new operation configured with a timeout policy."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=policy,
            _fallback_chain=self._fallback_chain,
            _circuit_breaker=self._circuit_breaker,
            _validation_policy=self._validation_policy,
            _event_emitter=self._event_emitter,
        )

    def with_fallbacks(self, chain: FallbackChain[P, R]) -> AsyncOperation[P, R]:
        """Return a new operation configured with fallback candidates."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=self._timeout_policy,
            _fallback_chain=chain,
            _circuit_breaker=self._circuit_breaker,
            _validation_policy=self._validation_policy,
            _event_emitter=self._event_emitter,
        )

    def with_circuit_breaker(self, breaker: CircuitBreaker) -> AsyncOperation[P, R]:
        """Return a new operation configured with a circuit breaker."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=self._timeout_policy,
            _fallback_chain=self._fallback_chain,
            _circuit_breaker=breaker,
            _validation_policy=self._validation_policy,
            _event_emitter=self._event_emitter,
        )

    def with_validation(self, policy: ValidationPolicy[R]) -> AsyncOperation[P, R]:
        """Return a new operation configured with result validation."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=self._timeout_policy,
            _fallback_chain=self._fallback_chain,
            _circuit_breaker=self._circuit_breaker,
            _validation_policy=policy,
            _event_emitter=self._event_emitter,
        )

    def with_events(self, emitter: EventEmitter) -> AsyncOperation[P, R]:
        """Return a new operation configured with a structured event emitter."""
        return AsyncOperation(
            name=self.name,
            _operation=self._operation,
            _retry_policy=self._retry_policy,
            _timeout_policy=self._timeout_policy,
            _fallback_chain=self._fallback_chain,
            _circuit_breaker=self._circuit_breaker,
            _validation_policy=self._validation_policy,
            _event_emitter=emitter,
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

        await self._emit_event(
            EventType.OPERATION_STARTED,
            payload={
                "retry_enabled": self._retry_policy is not None,
                "timeout_enabled": self._timeout_policy is not None,
                "fallback_enabled": self._fallback_chain is not None,
                "circuit_breaker_enabled": self._circuit_breaker is not None,
                "validation_enabled": self._validation_policy is not None,
            },
        )

        max_attempts = self._retry_policy.max_attempts if self._retry_policy is not None else 1

        for attempt_number in range(1, max_attempts + 1):
            attempt_started_at = _utc_now()
            attempt_started_monotonic = time.perf_counter()

            await self._emit_event(
                EventType.ATTEMPT_STARTED,
                payload={
                    "attempt_number": attempt_number,
                    "max_attempts": max_attempts,
                },
            )

            try:
                value, validation_metadata = await self._run_single_attempt(*args, **kwargs)
            except Exception as exc:
                attempt_status = self._attempt_status_for_exception(exc)
                retryable = self._is_retryable(exc)
                attempt_duration = _elapsed_seconds(attempt_started_monotonic)
                failure_metadata = self._primary_failure_metadata(exc)

                if isinstance(exc, ValidationFailedError):
                    await self._emit_event(
                        EventType.VALIDATION_FAILED,
                        payload={
                            "attempt_number": attempt_number,
                            "validator_name": exc.validator_name,
                            "reason": exc.reason,
                        },
                    )

                if isinstance(exc, CircuitBreakerOpenError):
                    await self._emit_event(
                        EventType.CIRCUIT_BREAKER_REJECTED,
                        payload={
                            "attempt_number": attempt_number,
                            "breaker_name": exc.breaker_name,
                            "state": exc.state,
                            "retry_after_seconds": exc.retry_after_seconds,
                        },
                    )

                attempts.append(
                    ExecutionAttempt(
                        attempt_number=attempt_number,
                        status=attempt_status,
                        started_at=attempt_started_at,
                        duration_seconds=attempt_duration,
                        error=ExecutionError.from_exception(exc, retryable=retryable),
                        metadata=failure_metadata,
                    )
                )

                await self._emit_event(
                    EventType.ATTEMPT_FAILED,
                    payload={
                        "attempt_number": attempt_number,
                        "duration_seconds": attempt_duration,
                        "error_type": exc.__class__.__name__,
                        "retryable": retryable,
                        **failure_metadata,
                    },
                )

                final_primary_failure = (not retryable) or attempt_number >= max_attempts

                if not final_primary_failure:
                    await self._sleep_before_retry(attempt_number)
                    continue

                if self._fallback_chain is not None:
                    fallback_attempt_number = len(attempts) + 1
                    fallback_started_at = _utc_now()
                    fallback_started_monotonic = time.perf_counter()

                    await self._emit_event(
                        EventType.FALLBACK_STARTED,
                        payload={
                            "attempt_number": fallback_attempt_number,
                            "after_primary_attempts": len(attempts),
                        },
                    )

                    try:
                        fallback_result = await self._fallback_chain.run(*args, **kwargs)
                        fallback_validation_metadata = self._validate_value(fallback_result.value)
                    except Exception as fallback_exc:
                        fallback_duration = _elapsed_seconds(fallback_started_monotonic)
                        fallback_metadata = self._fallback_failure_metadata(fallback_exc)

                        if isinstance(fallback_exc, ValidationFailedError):
                            await self._emit_event(
                                EventType.VALIDATION_FAILED,
                                payload={
                                    "attempt_number": fallback_attempt_number,
                                    "validator_name": fallback_exc.validator_name,
                                    "reason": fallback_exc.reason,
                                    "fallback_used": True,
                                },
                            )

                        attempts.append(
                            ExecutionAttempt(
                                attempt_number=fallback_attempt_number,
                                status=self._attempt_status_for_exception(fallback_exc),
                                started_at=fallback_started_at,
                                duration_seconds=fallback_duration,
                                error=ExecutionError.from_exception(
                                    fallback_exc,
                                    retryable=False,
                                ),
                                metadata=fallback_metadata,
                            )
                        )

                        await self._emit_event(
                            EventType.FALLBACK_FAILED,
                            payload={
                                "attempt_number": fallback_attempt_number,
                                "duration_seconds": fallback_duration,
                                "error_type": fallback_exc.__class__.__name__,
                                **fallback_metadata,
                            },
                        )

                        await self._emit_event(
                            EventType.ATTEMPT_FAILED,
                            payload={
                                "attempt_number": fallback_attempt_number,
                                "duration_seconds": fallback_duration,
                                "error_type": fallback_exc.__class__.__name__,
                                "retryable": False,
                                **fallback_metadata,
                            },
                        )

                        report = self._build_report(
                            status=self._report_status_for_attempt_status(attempts[-1].status),
                            started_at=operation_started_at,
                            started_monotonic=operation_started_monotonic,
                            attempts=attempts,
                            metadata=fallback_metadata,
                        )

                        await self._emit_event(
                            EventType.OPERATION_FAILED,
                            payload={
                                "duration_seconds": report.duration_seconds,
                                "attempt_count": report.attempt_count,
                                "retry_count": report.retry_count,
                                "error_type": fallback_exc.__class__.__name__,
                                **fallback_metadata,
                            },
                        )

                        raise OperationExecutionError(
                            f"Operation '{self.name}' failed and fallback chain failed.",
                            report=report,
                            cause=fallback_exc,
                        ) from fallback_exc

                    if fallback_validation_metadata["validation_performed"] is True:
                        await self._emit_event(
                            EventType.VALIDATION_SUCCEEDED,
                            payload={
                                "attempt_number": fallback_attempt_number,
                                "validator_name": fallback_validation_metadata[
                                    "validation_validator_name"
                                ],
                                "fallback_used": True,
                            },
                        )

                    fallback_metadata = self._fallback_success_metadata(
                        fallback_result,
                        validation_metadata=fallback_validation_metadata,
                    )
                    fallback_duration = _elapsed_seconds(fallback_started_monotonic)

                    attempts.append(
                        ExecutionAttempt(
                            attempt_number=fallback_attempt_number,
                            status=AttemptStatus.SUCCEEDED,
                            started_at=fallback_started_at,
                            duration_seconds=fallback_duration,
                            metadata=fallback_metadata,
                        )
                    )

                    await self._emit_event(
                        EventType.FALLBACK_SUCCEEDED,
                        payload={
                            "attempt_number": fallback_attempt_number,
                            "duration_seconds": fallback_duration,
                            **fallback_metadata,
                        },
                    )

                    await self._emit_event(
                        EventType.ATTEMPT_SUCCEEDED,
                        payload={
                            "attempt_number": fallback_attempt_number,
                            "duration_seconds": fallback_duration,
                            **fallback_metadata,
                        },
                    )

                    report = self._build_report(
                        status=ExecutionStatus.SUCCEEDED,
                        started_at=operation_started_at,
                        started_monotonic=operation_started_monotonic,
                        attempts=attempts,
                        metadata=fallback_metadata,
                    )

                    await self._emit_event(
                        EventType.OPERATION_SUCCEEDED,
                        payload={
                            "duration_seconds": report.duration_seconds,
                            "attempt_count": report.attempt_count,
                            "retry_count": report.retry_count,
                            **fallback_metadata,
                        },
                    )

                    return OperationResult(value=fallback_result.value, report=report)

                report = self._build_report(
                    status=self._report_status_for_attempt_status(attempt_status),
                    started_at=operation_started_at,
                    started_monotonic=operation_started_monotonic,
                    attempts=attempts,
                    metadata=failure_metadata,
                )

                await self._emit_event(
                    EventType.OPERATION_FAILED,
                    payload={
                        "duration_seconds": report.duration_seconds,
                        "attempt_count": report.attempt_count,
                        "retry_count": report.retry_count,
                        "error_type": exc.__class__.__name__,
                        **failure_metadata,
                    },
                )

                raise OperationExecutionError(
                    f"Operation '{self.name}' failed after {len(attempts)} attempt(s).",
                    report=report,
                    cause=exc,
                ) from exc

            if validation_metadata["validation_performed"] is True:
                await self._emit_event(
                    EventType.VALIDATION_SUCCEEDED,
                    payload={
                        "attempt_number": attempt_number,
                        "validator_name": validation_metadata["validation_validator_name"],
                    },
                )

            attempt_duration = _elapsed_seconds(attempt_started_monotonic)
            success_metadata = self._primary_success_metadata(validation_metadata)

            attempts.append(
                ExecutionAttempt(
                    attempt_number=attempt_number,
                    status=AttemptStatus.SUCCEEDED,
                    started_at=attempt_started_at,
                    duration_seconds=attempt_duration,
                    metadata=success_metadata,
                )
            )

            await self._emit_event(
                EventType.ATTEMPT_SUCCEEDED,
                payload={
                    "attempt_number": attempt_number,
                    "duration_seconds": attempt_duration,
                    **success_metadata,
                },
            )

            report = self._build_report(
                status=ExecutionStatus.SUCCEEDED,
                started_at=operation_started_at,
                started_monotonic=operation_started_monotonic,
                attempts=attempts,
                metadata=success_metadata,
            )

            await self._emit_event(
                EventType.OPERATION_SUCCEEDED,
                payload={
                    "duration_seconds": report.duration_seconds,
                    "attempt_count": report.attempt_count,
                    "retry_count": report.retry_count,
                    **success_metadata,
                },
            )

            return OperationResult(value=value, report=report)

        raise RuntimeError("AsyncOperation reached an invalid execution state.")

    async def _run_single_attempt(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> tuple[R, MutableMetadata]:
        if self._circuit_breaker is not None:
            return await self._circuit_breaker.run_async(
                self._run_single_attempt_without_circuit_breaker,
                *args,
                **kwargs,
            )

        return await self._run_single_attempt_without_circuit_breaker(*args, **kwargs)

    async def _run_single_attempt_without_circuit_breaker(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> tuple[R, MutableMetadata]:
        if self._timeout_policy is None:
            value = await self._operation(*args, **kwargs)
        else:
            value = await self._timeout_policy.run_async(self._operation, *args, **kwargs)

        validation_metadata = self._validate_value(value)

        return value, validation_metadata

    def _validate_value(self, value: R) -> MutableMetadata:
        if self._validation_policy is None:
            return self._no_validation_metadata()

        result = self._validation_policy.validate(value)

        if result.valid:
            return self._validation_success_metadata(result)

        reason = result.reason or "Validation failed."

        raise ValidationFailedError(
            f"Validation failed in '{result.validator_name}': {reason}",
            validator_name=result.validator_name,
            reason=reason,
        )

    async def _sleep_before_retry(self, attempt_number: int) -> None:
        if self._retry_policy is None:
            raise RuntimeError("Retry sleep requested without retry policy.")

        delay = self._retry_policy.backoff.delay_for_retry(attempt_number)

        await self._emit_event(
            EventType.RETRY_SCHEDULED,
            payload={
                "attempt_number": attempt_number,
                "next_attempt_number": attempt_number + 1,
                "delay_seconds": delay,
            },
        )

        await self._retry_policy.async_sleeper(delay)

    def _is_retryable(self, exception: Exception) -> bool:
        if self._retry_policy is None:
            return False

        return isinstance(exception, self._retry_policy.retry_on)

    async def _emit_event(
        self,
        event_type: EventType,
        *,
        payload: Mapping[str, MetadataValue] | None = None,
    ) -> None:
        if self._event_emitter is None:
            return

        await self._event_emitter.emit(
            event_type,
            operation_name=self.name,
            payload=payload,
        )

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
    def _validation_success_metadata(result: ValidationResult) -> MutableMetadata:
        return {
            "validation_performed": True,
            "validation_valid": True,
            "validation_validator_name": result.validator_name,
            "validation_reason": None,
        }

    @staticmethod
    def _validation_failure_metadata(exception: ValidationFailedError) -> MutableMetadata:
        return {
            "validation_performed": True,
            "validation_valid": False,
            "validation_validator_name": exception.validator_name,
            "validation_reason": exception.reason,
        }

    @staticmethod
    def _no_validation_metadata() -> MutableMetadata:
        return {
            "validation_performed": False,
            "validation_valid": None,
            "validation_validator_name": None,
            "validation_reason": None,
        }

    def _base_metadata(self, validation_metadata: Metadata | None = None) -> MutableMetadata:
        metadata: MutableMetadata = {
            "fallback_used": False,
            "circuit_breaker_open": False,
        }

        if validation_metadata is None:
            metadata.update(self._no_validation_metadata())
        else:
            metadata.update(validation_metadata)

        return metadata

    def _primary_success_metadata(self, validation_metadata: Metadata) -> MutableMetadata:
        return self._base_metadata(validation_metadata)

    def _primary_failure_metadata(self, exception: Exception) -> MutableMetadata:
        metadata = self._base_metadata()

        metadata["circuit_breaker_open"] = isinstance(exception, CircuitBreakerOpenError)

        if isinstance(exception, ValidationFailedError):
            metadata.update(self._validation_failure_metadata(exception))

        return metadata

    @staticmethod
    def _fallback_success_metadata(
        result: FallbackResult[R],
        *,
        validation_metadata: Metadata,
    ) -> MutableMetadata:
        metadata: MutableMetadata = {
            "fallback_used": True,
            "fallback_failed": False,
            "fallback_candidate_name": result.candidate_name,
            "fallback_candidate_index": result.candidate_index,
            "fallback_failure_count": result.failure_count,
            "circuit_breaker_open": False,
        }
        metadata.update(validation_metadata)

        return metadata

    def _fallback_failure_metadata(self, exception: Exception) -> MutableMetadata:
        failure_count = len(exception.failures) if isinstance(exception, FallbackChainError) else 0

        metadata: MutableMetadata = {
            "fallback_used": True,
            "fallback_failed": True,
            "fallback_failure_count": failure_count,
            "circuit_breaker_open": isinstance(exception, CircuitBreakerOpenError),
        }

        if isinstance(exception, ValidationFailedError):
            metadata.update(self._validation_failure_metadata(exception))
        else:
            metadata.update(self._no_validation_metadata())

        return metadata

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
