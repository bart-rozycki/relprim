from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
Metadata: TypeAlias = Mapping[str, JsonScalar]


class ExecutionStatus(StrEnum):
    """Final status of an operation execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class AttemptStatus(StrEnum):
    """Status of a single execution attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


def _empty_metadata() -> Metadata:
    return MappingProxyType({})


def _freeze_metadata(metadata: Mapping[str, JsonScalar] | None) -> Metadata:
    return MappingProxyType(dict(metadata or {}))


@dataclass(frozen=True, slots=True)
class ExecutionError:
    """Serializable error information captured during execution.

    RelPrim intentionally stores structured error metadata instead of raw exception
    objects inside reports. Raw exceptions are process-local and difficult to
    persist, export, or safely serialize.
    """

    type: str
    message: str
    module: str | None = None
    retryable: bool | None = None

    @classmethod
    def from_exception(
        cls,
        exception: BaseException,
        *,
        retryable: bool | None = None,
    ) -> ExecutionError:
        exception_type = exception.__class__

        return cls(
            type=exception_type.__name__,
            message=str(exception) or exception_type.__name__,
            module=exception_type.__module__,
            retryable=retryable,
        )

    def to_dict(self) -> dict[str, JsonScalar]:
        return {
            "type": self.type,
            "message": self.message,
            "module": self.module,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """Report entry for a single execution attempt."""

    attempt_number: int
    status: AttemptStatus
    started_at: datetime
    duration_seconds: float
    error: ExecutionError | None = None
    metadata: Metadata = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be greater than or equal to 1.")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be greater than or equal to 0.")
        if self.status is AttemptStatus.SUCCEEDED and self.error is not None:
            raise ValueError("successful attempts must not contain an error.")

        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def succeeded(self) -> bool:
        return self.status is AttemptStatus.SUCCEEDED

    @property
    def failed(self) -> bool:
        return not self.succeeded

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_number": self.attempt_number,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "error": self.error.to_dict() if self.error is not None else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Structured execution report for a resilient operation.

    The report is intentionally independent from retry, timeout, fallback,
    circuit breaker, OpenTelemetry, and storage concerns. It is the stable domain
    model those layers can build on later.
    """

    operation_name: str
    status: ExecutionStatus
    started_at: datetime
    duration_seconds: float
    attempts: tuple[ExecutionAttempt, ...]
    metadata: Metadata = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.operation_name.strip():
            raise ValueError("operation_name must not be empty.")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be greater than or equal to 0.")
        if not self.attempts:
            raise ValueError("attempts must contain at least one execution attempt.")

        last_attempt = self.attempts[-1]

        if (
            self.status is ExecutionStatus.SUCCEEDED
            and last_attempt.status is not AttemptStatus.SUCCEEDED
        ):
            raise ValueError("successful reports must end with a successful attempt.")

        if (
            self.status is not ExecutionStatus.SUCCEEDED
            and last_attempt.status is AttemptStatus.SUCCEEDED
        ):
            raise ValueError("failed reports must not end with a successful attempt.")

        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def succeeded(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED

    @property
    def failed(self) -> bool:
        return not self.succeeded

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def retry_count(self) -> int:
        return max(0, self.attempt_count - 1)

    @property
    def retried(self) -> bool:
        return self.retry_count > 0

    @property
    def last_attempt(self) -> ExecutionAttempt:
        return self.attempts[-1]

    @property
    def last_error(self) -> ExecutionError | None:
        for attempt in reversed(self.attempts):
            if attempt.error is not None:
                return attempt.error

        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_name": self.operation_name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "attempt_count": self.attempt_count,
            "retry_count": self.retry_count,
            "retried": self.retried,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "last_error": self.last_error.to_dict() if self.last_error is not None else None,
            "metadata": dict(self.metadata),
        }
