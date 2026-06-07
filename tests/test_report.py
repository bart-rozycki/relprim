from __future__ import annotations

from datetime import UTC, datetime

import pytest

from relprim import (
    AttemptStatus,
    ExecutionAttempt,
    ExecutionError,
    ExecutionReport,
    ExecutionStatus,
)


class TransientError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def successful_attempt(*, attempt_number: int = 1) -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_number=attempt_number,
        status=AttemptStatus.SUCCEEDED,
        started_at=utc_now(),
        duration_seconds=0.1,
    )


def failed_attempt(*, attempt_number: int = 1) -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_number=attempt_number,
        status=AttemptStatus.FAILED,
        started_at=utc_now(),
        duration_seconds=0.1,
        error=ExecutionError(
            type="TransientError",
            message="temporary failure",
            module="tests.test_report",
            retryable=True,
        ),
    )


def test_execution_error_can_be_created_from_exception() -> None:
    error = ExecutionError.from_exception(
        TransientError("temporary failure"),
        retryable=True,
    )

    assert error.type == "TransientError"
    assert error.message == "temporary failure"
    assert error.module in {"test_report", "tests.test_report"}
    assert error.retryable is True


def test_execution_attempt_exposes_success_flags() -> None:
    attempt = successful_attempt()

    assert attempt.succeeded is True
    assert attempt.failed is False


def test_execution_attempt_rejects_invalid_attempt_number() -> None:
    with pytest.raises(ValueError):
        ExecutionAttempt(
            attempt_number=0,
            status=AttemptStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=0.1,
        )


def test_execution_attempt_rejects_negative_duration() -> None:
    with pytest.raises(ValueError):
        ExecutionAttempt(
            attempt_number=1,
            status=AttemptStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=-0.1,
        )


def test_execution_attempt_rejects_error_on_successful_attempt() -> None:
    with pytest.raises(ValueError):
        ExecutionAttempt(
            attempt_number=1,
            status=AttemptStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=0.1,
            error=ExecutionError(type="Error", message="should not be here"),
        )


def test_execution_attempt_freezes_metadata() -> None:
    metadata = {"provider": "openai"}

    attempt = ExecutionAttempt(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        started_at=utc_now(),
        duration_seconds=0.1,
        metadata=metadata,
    )

    metadata["provider"] = "gemini"

    assert attempt.metadata["provider"] == "openai"

    with pytest.raises(TypeError):
        attempt.metadata["provider"] = "anthropic"  # type: ignore[index]


def test_successful_execution_report_exposes_summary_properties() -> None:
    report = ExecutionReport(
        operation_name="generate_summary",
        status=ExecutionStatus.SUCCEEDED,
        started_at=utc_now(),
        duration_seconds=0.25,
        attempts=(failed_attempt(attempt_number=1), successful_attempt(attempt_number=2)),
    )

    assert report.succeeded is True
    assert report.failed is False
    assert report.attempt_count == 2
    assert report.retry_count == 1
    assert report.retried is True
    assert report.last_attempt.succeeded is True
    assert report.last_error is not None
    assert report.last_error.type == "TransientError"


def test_failed_execution_report_exposes_summary_properties() -> None:
    report = ExecutionReport(
        operation_name="generate_summary",
        status=ExecutionStatus.FAILED,
        started_at=utc_now(),
        duration_seconds=0.25,
        attempts=(failed_attempt(attempt_number=1), failed_attempt(attempt_number=2)),
    )

    assert report.succeeded is False
    assert report.failed is True
    assert report.attempt_count == 2
    assert report.retry_count == 1
    assert report.retried is True
    assert report.last_attempt.failed is True
    assert report.last_error is not None


def test_execution_report_rejects_empty_operation_name() -> None:
    with pytest.raises(ValueError):
        ExecutionReport(
            operation_name=" ",
            status=ExecutionStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=0.1,
            attempts=(successful_attempt(),),
        )


def test_execution_report_rejects_negative_duration() -> None:
    with pytest.raises(ValueError):
        ExecutionReport(
            operation_name="generate_summary",
            status=ExecutionStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=-0.1,
            attempts=(successful_attempt(),),
        )


def test_execution_report_requires_at_least_one_attempt() -> None:
    with pytest.raises(ValueError):
        ExecutionReport(
            operation_name="generate_summary",
            status=ExecutionStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=0.1,
            attempts=(),
        )


def test_successful_execution_report_must_end_with_successful_attempt() -> None:
    with pytest.raises(ValueError):
        ExecutionReport(
            operation_name="generate_summary",
            status=ExecutionStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=0.1,
            attempts=(failed_attempt(),),
        )


def test_failed_execution_report_must_not_end_with_successful_attempt() -> None:
    with pytest.raises(ValueError):
        ExecutionReport(
            operation_name="generate_summary",
            status=ExecutionStatus.FAILED,
            started_at=utc_now(),
            duration_seconds=0.1,
            attempts=(successful_attempt(),),
        )


def test_execution_report_freezes_metadata() -> None:
    metadata = {"workflow": "content_generation"}

    report = ExecutionReport(
        operation_name="generate_summary",
        status=ExecutionStatus.SUCCEEDED,
        started_at=utc_now(),
        duration_seconds=0.1,
        attempts=(successful_attempt(),),
        metadata=metadata,
    )

    metadata["workflow"] = "moderation"

    assert report.metadata["workflow"] == "content_generation"

    with pytest.raises(TypeError):
        report.metadata["workflow"] = "rag"  # type: ignore[index]


def test_execution_report_can_be_serialized_to_dict() -> None:
    report = ExecutionReport(
        operation_name="generate_summary",
        status=ExecutionStatus.SUCCEEDED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_seconds=0.25,
        attempts=(failed_attempt(attempt_number=1), successful_attempt(attempt_number=2)),
        metadata={"provider": "openai"},
    )

    payload = report.to_dict()

    assert payload["operation_name"] == "generate_summary"
    assert payload["status"] == "succeeded"
    assert payload["duration_seconds"] == 0.25
    assert payload["attempt_count"] == 2
    assert payload["retry_count"] == 1
    assert payload["retried"] is True
    assert payload["metadata"] == {"provider": "openai"}
    assert isinstance(payload["attempts"], list)
    assert payload["last_error"] is not None
