from __future__ import annotations

from relprim.report import ExecutionReport


class RelPrimError(Exception):
    """Base exception for all RelPrim errors."""


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
