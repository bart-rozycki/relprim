from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from relprim import (
    IdempotencyReentryError,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
    idempotency_policy,
)


class TemporaryError(Exception):
    pass


@dataclass
class ManualClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_idempotency_store_executes_operation_for_new_key() -> None:
    store = InMemoryIdempotencyStore()

    async def operation() -> str:
        return "created"

    result = await store.execute(
        "order-123",
        operation,
        ttl_seconds=60,
    )

    assert result.value == "created"
    assert result.key == "order-123"
    assert result.status is IdempotencyStatus.EXECUTED
    assert result.executed is True
    assert result.joined is False
    assert result.replayed is False
    assert result.cache_hit is False


async def test_idempotency_store_replays_successful_result() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "created"

    first = await store.execute(
        "order-123",
        operation,
        ttl_seconds=60,
    )
    second = await store.execute(
        "order-123",
        operation,
        ttl_seconds=60,
    )

    assert first.status is IdempotencyStatus.EXECUTED
    assert second.status is IdempotencyStatus.REPLAYED
    assert second.value == "created"
    assert second.cache_hit is True
    assert calls == 1


async def test_idempotency_store_joins_concurrent_execution() -> None:
    store = InMemoryIdempotencyStore()
    operation_started = asyncio.Event()
    release_operation = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        operation_started.set()
        await release_operation.wait()
        return "created"

    first_task = asyncio.create_task(
        store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )
    )

    await operation_started.wait()

    second_task = asyncio.create_task(
        store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )
    )

    await asyncio.sleep(0)
    release_operation.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert first.status is IdempotencyStatus.EXECUTED
    assert second.status is IdempotencyStatus.JOINED
    assert first.value == "created"
    assert second.value == "created"
    assert calls == 1


async def test_idempotency_store_executes_again_after_expiry() -> None:
    clock = ManualClock()
    store = InMemoryIdempotencyStore(clock=clock)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return f"result-{calls}"

    first = await store.execute(
        "order-123",
        operation,
        ttl_seconds=10,
    )

    clock.advance(11)

    second = await store.execute(
        "order-123",
        operation,
        ttl_seconds=10,
    )

    assert first.value == "result-1"
    assert second.value == "result-2"
    assert second.status is IdempotencyStatus.EXECUTED
    assert calls == 2


async def test_idempotency_store_does_not_cache_failures() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise TemporaryError("temporary failure")

        return "created"

    with pytest.raises(TemporaryError, match="temporary failure"):
        await store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )

    result = await store.execute(
        "order-123",
        operation,
        ttl_seconds=60,
    )

    assert result.value == "created"
    assert result.status is IdempotencyStatus.EXECUTED
    assert calls == 2


async def test_concurrent_callers_receive_same_failure() -> None:
    store = InMemoryIdempotencyStore()
    operation_started = asyncio.Event()
    release_operation = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        operation_started.set()
        await release_operation.wait()
        raise TemporaryError("temporary failure")

    first_task = asyncio.create_task(
        store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )
    )

    await operation_started.wait()

    second_task = asyncio.create_task(
        store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )
    )

    await asyncio.sleep(0)
    release_operation.set()

    results = await asyncio.gather(
        first_task,
        second_task,
        return_exceptions=True,
    )

    assert calls == 1
    assert len(results) == 2
    assert all(isinstance(result, TemporaryError) for result in results)


async def test_waiter_cancellation_does_not_cancel_shared_execution() -> None:
    store = InMemoryIdempotencyStore()
    operation_started = asyncio.Event()
    release_operation = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        operation_started.set()
        await release_operation.wait()
        return "created"

    owner_task = asyncio.create_task(
        store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )
    )

    await operation_started.wait()

    waiter_task = asyncio.create_task(
        store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )
    )

    await asyncio.sleep(0)
    waiter_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    release_operation.set()

    owner_result = await owner_task
    replayed_result = await store.execute(
        "order-123",
        operation,
        ttl_seconds=60,
    )

    assert owner_result.status is IdempotencyStatus.EXECUTED
    assert replayed_result.status is IdempotencyStatus.REPLAYED
    assert calls == 1


async def test_owner_cancellation_releases_idempotency_key() -> None:
    store = InMemoryIdempotencyStore()
    operation_started = asyncio.Event()
    wait_forever = asyncio.Event()

    async def cancelled_operation() -> str:
        operation_started.set()
        await wait_forever.wait()
        return "unreachable"

    owner_task = asyncio.create_task(
        store.execute(
            "order-123",
            cancelled_operation,
            ttl_seconds=60,
        )
    )

    await operation_started.wait()

    owner_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await owner_task

    async def replacement_operation() -> str:
        return "created"

    result = await store.execute(
        "order-123",
        replacement_operation,
        ttl_seconds=60,
    )

    assert result.value == "created"
    assert result.status is IdempotencyStatus.EXECUTED


async def test_idempotency_store_rejects_recursive_use_of_owned_key() -> None:
    store = InMemoryIdempotencyStore()

    async def nested_operation() -> str:
        return "nested"

    async def operation() -> str:
        await store.execute(
            "order-123",
            nested_operation,
            ttl_seconds=60,
        )
        return "outer"

    with pytest.raises(IdempotencyReentryError) as exc_info:
        await store.execute(
            "order-123",
            operation,
            ttl_seconds=60,
        )

    assert exc_info.value.key == "order-123"


async def test_idempotency_policy_derives_key_from_operation_arguments() -> None:
    store = InMemoryIdempotencyStore()

    policy = idempotency_policy(
        lambda order_id, amount: f"{order_id}:{amount}",
        store=store,
        ttl_seconds=60,
    )

    calls = 0

    async def create_payment(order_id: str, amount: int) -> str:
        nonlocal calls
        calls += 1
        return f"payment:{order_id}:{amount}"

    first = await policy.run(create_payment, "order-123", 100)
    second = await policy.run(create_payment, "order-123", 100)

    assert first.status is IdempotencyStatus.EXECUTED
    assert second.status is IdempotencyStatus.REPLAYED
    assert second.key == "order-123:100"
    assert second.value == "payment:order-123:100"
    assert calls == 1


@pytest.mark.parametrize(
    "key",
    [
        "",
        " ",
        "\n",
    ],
)
async def test_idempotency_store_rejects_empty_key(key: str) -> None:
    store = InMemoryIdempotencyStore()

    async def operation() -> str:
        return "created"

    with pytest.raises(ValueError, match="idempotency key must not be empty"):
        await store.execute(
            key,
            operation,
            ttl_seconds=60,
        )


async def test_idempotency_policy_rejects_non_string_key() -> None:
    policy = idempotency_policy(
        lambda value: value,  # type: ignore[arg-type,return-value]
    )

    async def operation(value: int) -> int:
        return value

    with pytest.raises(TypeError, match="idempotency key must be a string"):
        await policy.run(operation, 123)


@pytest.mark.parametrize("ttl_seconds", [0, -1, -0.1])
def test_idempotency_policy_rejects_non_positive_ttl(ttl_seconds: float) -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be greater than 0"):
        idempotency_policy(
            lambda value: value,
            ttl_seconds=ttl_seconds,
        )


async def test_purge_expired_removes_only_expired_entries() -> None:
    clock = ManualClock()
    store = InMemoryIdempotencyStore(clock=clock)

    async def operation() -> str:
        return "created"

    await store.execute(
        "order-123",
        operation,
        ttl_seconds=10,
    )
    await store.execute(
        "order-456",
        operation,
        ttl_seconds=20,
    )

    clock.advance(11)

    removed = await store.purge_expired()

    assert removed == 1
    assert await store.entry_count() == 1
