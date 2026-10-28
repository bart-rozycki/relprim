from __future__ import annotations

import asyncio

import pytest

from relprim import OperationTimeoutError, TimeoutPolicy


async def test_run_async_returns_result_before_timeout() -> None:
    policy = TimeoutPolicy(seconds=1.0)

    async def operation() -> str:
        return "ok"

    result = await policy.run_async(operation)

    assert result == "ok"


async def test_run_async_passes_args_and_kwargs() -> None:
    policy = TimeoutPolicy(seconds=1.0)

    async def operation(prefix: str, *, value: int) -> str:
        return f"{prefix}-{value}"

    result = await policy.run_async(operation, "item", value=42)

    assert result == "item-42"


async def test_run_async_raises_timeout_error_when_operation_exceeds_timeout() -> None:
    policy = TimeoutPolicy(seconds=0.01)

    async def operation() -> str:
        await asyncio.sleep(1)
        return "ok"

    with pytest.raises(OperationTimeoutError) as exc_info:
        await policy.run_async(operation)

    assert exc_info.value.timeout_seconds == 0.01
    assert isinstance(exc_info.value.cause, TimeoutError)


async def test_run_async_cancels_timed_out_operation() -> None:
    policy = TimeoutPolicy(seconds=0.01)
    cancelled = False

    async def operation() -> str:
        nonlocal cancelled

        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled = True
            raise

        return "ok"

    with pytest.raises(OperationTimeoutError):
        await policy.run_async(operation)

    assert cancelled is True


@pytest.mark.parametrize("seconds", [0, -1])
def test_timeout_policy_rejects_invalid_timeout(seconds: float) -> None:
    with pytest.raises(ValueError):
        TimeoutPolicy(seconds=seconds)


async def test_operation_own_timeout_error_is_not_converted() -> None:
    async def operation() -> str:
        raise TimeoutError("client socket timeout")

    with pytest.raises(TimeoutError) as exc_info:
        await TimeoutPolicy(seconds=60).run_async(operation)

    assert type(exc_info.value) is TimeoutError
    assert str(exc_info.value) == "client socket timeout"


@pytest.mark.parametrize("seconds", [float("nan"), float("inf")])
def test_timeout_policy_rejects_non_finite_timeout(seconds: float) -> None:
    with pytest.raises(ValueError, match="seconds must be finite"):
        TimeoutPolicy(seconds=seconds)


def test_timeout_policy_rejects_bool_timeout() -> None:
    with pytest.raises(TypeError, match="seconds must be an int or float"):
        TimeoutPolicy(seconds=True)
