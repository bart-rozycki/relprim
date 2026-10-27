from __future__ import annotations

from typing import TYPE_CHECKING

from relprim.report import ExecutionReport

if TYPE_CHECKING:
    from relprim.circuit_breaker import CircuitBreakerState


class RelPrimError(Exception):
    """Base exception for all RelPrim errors.

    Configuration mistakes are reported with built-in ValueError and TypeError
    exceptions instead, and cancellation always propagates as
    asyncio.CancelledError.
    """


class RetryError(RelPrimError):
    """Raised when an operation fails after all retry attempts."""

    def __init__(self, message: str, *, attempts: int, cause: BaseException) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.cause = cause


class OperationTimeoutError(RelPrimError):
    """Raised when an operation exceeds its configured timeout."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.cause = cause


class OperationExecutionError(RelPrimError):
    """Raised when a resilient operation fails.

    The execution report is attached so callers can inspect attempts, duration,
    retry count and the final failure reason without relying on logs.
    """

    def __init__(
        self,
        message: str,
        *,
        report: ExecutionReport,
        cause: BaseException,
    ) -> None:
        super().__init__(message)
        self.report = report
        self.cause = cause


class FallbackChainError(RelPrimError):
    """Raised when all fallback candidates fail.

    The original exceptions are preserved so callers can inspect the full
    failure path instead of only seeing the last error.
    """

    def __init__(
        self,
        message: str,
        *,
        failures: tuple[BaseException, ...],
    ) -> None:
        super().__init__(message)
        self.failures = failures
        self.cause = failures[-1] if failures else None


class CircuitBreakerOpenError(RelPrimError):
    """Raised when a circuit breaker rejects an operation."""

    def __init__(
        self,
        message: str,
        *,
        breaker_name: str,
        state: CircuitBreakerState,
        retry_after_seconds: float | None,
    ) -> None:
        super().__init__(message)
        self.breaker_name = breaker_name
        self.state = state
        self.retry_after_seconds = retry_after_seconds


class ValidationFailedError(RelPrimError):
    """Raised when value validation fails."""

    def __init__(
        self,
        message: str,
        *,
        validator_name: str,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.validator_name = validator_name
        self.reason = reason


class IdempotencyReentryError(RelPrimError):
    """Raised when an operation recursively uses the same idempotency key.

    The key is available as the `key` attribute but is intentionally not part
    of the message, because idempotency keys may contain sensitive identifiers
    and exception messages commonly end up in logs.
    """

    def __init__(self, key: str) -> None:
        super().__init__("An idempotency key was reused recursively by its owning task.")
        self.key = key


class RetryAfterExtractionError(RelPrimError):
    """Raised when a retry-after value cannot be extracted safely."""

    def __init__(
        self,
        message: str,
        *,
        rate_limit_error: Exception,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.rate_limit_error = rate_limit_error
        self.cause = cause


class SQLiteEventStoreSchemaError(RelPrimError):
    """Raised when a SQLite event-store schema cannot be used safely."""
