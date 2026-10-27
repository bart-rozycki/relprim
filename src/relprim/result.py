from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from relprim.report import ExecutionReport

T_co = TypeVar("T_co", covariant=True)


@dataclass(frozen=True, slots=True)
class OperationResult(Generic[T_co]):
    """Result of a resilient operation execution.

    OperationResult keeps the business value and execution report together.

    OperationResult is only returned for successful executions; failed
    executions raise OperationExecutionError with the report attached. The
    `succeeded` and `failed` properties mirror the report for convenience.

    Low-level primitives such as RetryPolicy and TimeoutPolicy return raw values
    directly.
    """

    value: T_co
    report: ExecutionReport

    @property
    def succeeded(self) -> bool:
        return self.report.succeeded

    @property
    def failed(self) -> bool:
        return self.report.failed
