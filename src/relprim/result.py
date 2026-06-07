from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from relprim.report import ExecutionReport

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OperationResult(Generic[T]):
    """Result of a resilient operation execution.

    OperationResult keeps the business value and execution report together.

    Low-level primitives such as RetryPolicy and TimeoutPolicy may still return
    raw values directly. Higher-level APIs can return OperationResult when users
    need observability data alongside the actual result.
    """

    value: T
    report: ExecutionReport

    @property
    def succeeded(self) -> bool:
        return self.report.succeeded

    @property
    def failed(self) -> bool:
        return self.report.failed
