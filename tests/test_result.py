from __future__ import annotations

from datetime import UTC, datetime

from relprim import (
    AttemptStatus,
    ExecutionAttempt,
    ExecutionReport,
    ExecutionStatus,
    OperationResult,
)


def successful_report() -> ExecutionReport:
    return ExecutionReport(
        operation_name="generate_summary",
        status=ExecutionStatus.SUCCEEDED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_seconds=0.1,
        attempts=(
            ExecutionAttempt(
                attempt_number=1,
                status=AttemptStatus.SUCCEEDED,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                duration_seconds=0.1,
            ),
        ),
    )


def failed_report() -> ExecutionReport:
    return ExecutionReport(
        operation_name="generate_summary",
        status=ExecutionStatus.FAILED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_seconds=0.1,
        attempts=(
            ExecutionAttempt(
                attempt_number=1,
                status=AttemptStatus.FAILED,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                duration_seconds=0.1,
            ),
        ),
    )


def test_operation_result_stores_value_and_report() -> None:
    report = successful_report()

    result = OperationResult(value="summary", report=report)

    assert result.value == "summary"
    assert result.report is report


def test_operation_result_exposes_success_flags_from_report() -> None:
    result = OperationResult(value="summary", report=successful_report())

    assert result.succeeded is True
    assert result.failed is False


def test_operation_result_exposes_failure_flags_from_report() -> None:
    result = OperationResult(value=None, report=failed_report())

    assert result.succeeded is False
    assert result.failed is True
