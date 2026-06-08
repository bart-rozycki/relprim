from __future__ import annotations

import asyncio

import pytest

from relprim import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
)


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class ManualClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_circuit_breaker_starts_closed() -> None:
    breaker = CircuitBreaker(name="provider")

    snapshot = await breaker.snapshot()

    assert snapshot.name == "provider"
    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0
    assert snapshot.closed is True


async def test_circuit_breaker_allows_successful_call() -> None:
    breaker = CircuitBreaker(name="provider")

    async def operation() -> str:
        return "ok"

    result = await breaker.run_async(operation)

    snapshot = await breaker.snapshot()

    assert result == "ok"
    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0


async def test_circuit_breaker_counts_recorded_failures() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=3,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(operation)

    snapshot = await breaker.snapshot()

    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 1


async def test_circuit_breaker_opens_after_failure_threshold() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=2,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(operation)

    with pytest.raises(TransientError):
        await breaker.run_async(operation)

    snapshot = await breaker.snapshot()

    assert snapshot.state is CircuitBreakerState.OPEN
    assert snapshot.failure_count == 2
    assert snapshot.opened_at is not None


async def test_open_circuit_breaker_rejects_call_without_running_operation() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        recovery_timeout_seconds=30.0,
        record_failure_on=(TransientError,),
    )

    calls = 0

    async def failing_operation() -> str:
        nonlocal calls
        calls += 1
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(failing_operation)

    async def should_not_run() -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        await breaker.run_async(should_not_run)

    error = exc_info.value

    assert calls == 1
    assert error.breaker_name == "provider"
    assert error.state == "open"
    assert error.retry_after_seconds is not None


async def test_circuit_breaker_transitions_to_half_open_after_recovery_timeout() -> None:
    clock = ManualClock()

    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        record_failure_on=(TransientError,),
        clock=clock,
    )

    async def failing_operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(failing_operation)

    clock.advance(10.0)

    async def recovery_probe() -> str:
        return "recovered"

    result = await breaker.run_async(recovery_probe)
    snapshot = await breaker.snapshot()

    assert result == "recovered"
    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0
    assert snapshot.opened_at is None


async def test_half_open_failure_reopens_circuit_breaker() -> None:
    clock = ManualClock()

    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        record_failure_on=(TransientError,),
        clock=clock,
    )

    async def failing_operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(failing_operation)

    clock.advance(10.0)

    with pytest.raises(TransientError):
        await breaker.run_async(failing_operation)

    snapshot = await breaker.snapshot()

    assert snapshot.state is CircuitBreakerState.OPEN
    assert snapshot.failure_count == 1
    assert snapshot.opened_at == 10.0


async def test_half_open_allows_only_one_probe_at_a_time() -> None:
    clock = ManualClock()

    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        record_failure_on=(TransientError,),
        clock=clock,
    )

    async def failing_operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(failing_operation)

    clock.advance(10.0)

    probe_started = asyncio.Event()
    allow_probe_to_finish = asyncio.Event()

    async def slow_probe() -> str:
        probe_started.set()
        await allow_probe_to_finish.wait()
        return "recovered"

    first_probe = asyncio.create_task(breaker.run_async(slow_probe))

    await probe_started.wait()

    async def second_probe() -> str:
        return "should not run"

    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        await breaker.run_async(second_probe)

    assert exc_info.value.state == "half_open"

    allow_probe_to_finish.set()

    assert await first_probe == "recovered"


async def test_non_recorded_exception_does_not_open_breaker() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        raise PermanentError("not recorded")

    with pytest.raises(PermanentError):
        await breaker.run_async(operation)

    snapshot = await breaker.snapshot()

    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0


async def test_success_resets_failure_count_in_closed_state() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=3,
        record_failure_on=(TransientError,),
    )

    async def failing_operation() -> str:
        raise TransientError("temporary failure")

    async def successful_operation() -> str:
        return "ok"

    with pytest.raises(TransientError):
        await breaker.run_async(failing_operation)

    result = await breaker.run_async(successful_operation)
    snapshot = await breaker.snapshot()

    assert result == "ok"
    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0


async def test_reset_closes_open_breaker() -> None:
    breaker = CircuitBreaker(
        name="provider",
        failure_threshold=1,
        record_failure_on=(TransientError,),
    )

    async def operation() -> str:
        raise TransientError("temporary failure")

    with pytest.raises(TransientError):
        await breaker.run_async(operation)

    await breaker.reset()

    snapshot = await breaker.snapshot()

    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.failure_count == 0
    assert snapshot.opened_at is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": " "},
        {"failure_threshold": 0},
        {"recovery_timeout_seconds": 0},
        {"record_failure_on": ()},
    ],
)
def test_circuit_breaker_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(**kwargs)
