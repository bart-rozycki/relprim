"""Builders and policies are immutable; shared state is always explicit."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from relprim import (
    EventEmitter,
    ExponentialBackoff,
    FallbackPolicy,
    IdempotencyPolicy,
    InMemoryEventSink,
    RetryPolicy,
    TimeoutPolicy,
    async_operation,
    fallback_chain,
    idempotency_policy,
    resilient,
    validation_policy,
    validator,
)


async def ok() -> str:
    return "ok"


def test_builder_methods_leave_the_original_operation_unchanged() -> None:
    base = async_operation("provider_call", ok)
    configured = (
        base.with_retry(RetryPolicy(max_attempts=2))
        .with_timeout(TimeoutPolicy(seconds=1))
        .with_validation(validation_policy(validator("v", lambda value: True, message="m")))
        .with_fallbacks(fallback_chain(("backup", ok)))
        .with_events(EventEmitter())
    )

    assert configured is not base
    assert base == async_operation("provider_call", ok)
    assert configured != base


@pytest.mark.parametrize(
    "value",
    [
        RetryPolicy(),
        ExponentialBackoff(),
        TimeoutPolicy(seconds=1),
        FallbackPolicy(),
        fallback_chain(("backup", ok)),
        validation_policy(validator("v", lambda value: True, message="m")),
        idempotency_policy(lambda: "key"),
        EventEmitter(),
        async_operation("provider_call", ok),
    ],
    ids=lambda value: type(value).__name__,
)
def test_policies_are_frozen(value: object) -> None:
    attribute = next(iter(value.__dataclass_fields__))  # type: ignore[attr-defined]

    with pytest.raises(FrozenInstanceError):
        setattr(value, attribute, None)


def test_default_idempotency_stores_are_not_shared() -> None:
    first = idempotency_policy(lambda: "key")
    second = idempotency_policy(lambda: "key")
    direct = IdempotencyPolicy(key_factory=lambda: "key")

    assert first.store is not second.store
    assert direct.store is not first.store


async def test_decorated_functions_get_separate_default_idempotency_stores() -> None:
    calls: list[str] = []

    @resilient(idempotency_key=lambda: "same-key")
    async def first() -> str:
        calls.append("first")
        return "first"

    @resilient(idempotency_key=lambda: "same-key")
    async def second() -> str:
        calls.append("second")
        return "second"

    assert (await first()).value == "first"
    assert (await second()).value == "second"
    assert calls == ["first", "second"]


async def test_shared_emitter_is_shared_deliberately() -> None:
    sink = InMemoryEventSink()
    emitter = EventEmitter(sinks=(sink,))

    await async_operation("a", ok).with_events(emitter).run()
    await async_operation("b", ok).with_events(emitter).run()

    assert {event.operation_name for event in await sink.events()} == {"a", "b"}


async def test_running_an_operation_does_not_change_its_configuration() -> None:
    configured = async_operation("provider_call", ok).with_retry(RetryPolicy(max_attempts=2))
    snapshot = repr(configured)

    await configured.run()

    assert repr(configured) == snapshot
