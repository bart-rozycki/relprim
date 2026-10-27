from __future__ import annotations

from datetime import UTC, datetime, tzinfo

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


def test_reports_reject_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="started_at must be timezone-aware"):
        ExecutionAttempt(
            attempt_number=1,
            status=AttemptStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1),
            duration_seconds=0.1,
        )


@pytest.mark.parametrize(
    ("metadata", "error"),
    [
        ({"value": float("nan")}, ValueError),
        ({"value": object()}, TypeError),
        ({"value": [1, 2]}, TypeError),
        ({1: "value"}, TypeError),
    ],
)
def test_report_metadata_must_contain_json_scalars(
    metadata: dict[object, object],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        ExecutionAttempt(
            attempt_number=1,
            status=AttemptStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            duration_seconds=0.1,
            metadata=metadata,  # type: ignore[arg-type]
        )


def test_report_metadata_is_copied_not_shared() -> None:
    source: dict[str, str | int | float | bool | None] = {"key": "value"}
    attempt = ExecutionAttempt(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_seconds=0.1,
        metadata=source,
    )

    source["key"] = "changed"
    exported = attempt.to_dict()["metadata"]
    assert isinstance(exported, dict)
    exported["key"] = "mutated"

    assert attempt.metadata["key"] == "value"


def test_retry_count_does_not_count_fallback_attempts() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    failed = ExecutionError(type="TransientError", message="down")

    report = ExecutionReport(
        operation_name="provider_call",
        status=ExecutionStatus.SUCCEEDED,
        started_at=started_at,
        duration_seconds=0.3,
        attempts=(
            ExecutionAttempt(1, AttemptStatus.FAILED, started_at, 0.1, error=failed),
            ExecutionAttempt(2, AttemptStatus.FAILED, started_at, 0.1, error=failed),
            ExecutionAttempt(3, AttemptStatus.SUCCEEDED, started_at, 0.1, fallback=True),
        ),
    )

    assert report.attempt_count == 3
    assert report.retry_count == 1
    assert report.retried is True
    assert report.to_dict()["retry_count"] == 1
    assert report.attempts[-1].to_dict()["fallback"] is True


class NoOffsetTimezone(tzinfo):
    """A tzinfo that makes datetimes naive: utcoffset() returns None."""

    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> str:
        return "unknown"


def test_reports_reject_timestamps_without_utc_offset() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=NoOffsetTimezone())

    with pytest.raises(ValueError, match="started_at must be timezone-aware"):
        ExecutionAttempt(1, AttemptStatus.SUCCEEDED, started_at, 0.1)

    with pytest.raises(ValueError, match="started_at must be timezone-aware"):
        ExecutionReport(
            operation_name="provider_call",
            status=ExecutionStatus.SUCCEEDED,
            started_at=started_at,
            duration_seconds=0.1,
            attempts=(successful_attempt(),),
        )


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_execution_attempt_rejects_non_finite_duration(duration: float) -> None:
    with pytest.raises(ValueError, match="duration_seconds must be finite"):
        ExecutionAttempt(1, AttemptStatus.SUCCEEDED, utc_now(), duration)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_execution_report_rejects_non_finite_duration(duration: float) -> None:
    with pytest.raises(ValueError, match="duration_seconds must be finite"):
        ExecutionReport(
            operation_name="provider_call",
            status=ExecutionStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=duration,
            attempts=(successful_attempt(),),
        )


@pytest.mark.parametrize("duration", [True, "0.1", None])
def test_report_durations_must_be_numbers(duration: object) -> None:
    with pytest.raises(TypeError, match="duration_seconds must be an int or float"):
        ExecutionAttempt(1, AttemptStatus.SUCCEEDED, utc_now(), duration)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="duration_seconds must be an int or float"):
        ExecutionReport(
            operation_name="provider_call",
            status=ExecutionStatus.SUCCEEDED,
            started_at=utc_now(),
            duration_seconds=duration,  # type: ignore[arg-type]
            attempts=(successful_attempt(),),
        )


@pytest.mark.parametrize("duration", [0, 0.0, 1, 2.5])
def test_report_durations_accept_finite_non_negative_numbers(duration: float) -> None:
    attempt = ExecutionAttempt(1, AttemptStatus.SUCCEEDED, utc_now(), duration)
    report = ExecutionReport(
        operation_name="provider_call",
        status=ExecutionStatus.SUCCEEDED,
        started_at=utc_now(),
        duration_seconds=duration,
        attempts=(attempt,),
    )

    assert attempt.duration_seconds == duration
    assert report.duration_seconds == duration
