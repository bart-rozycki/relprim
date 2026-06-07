from __future__ import annotations

import pytest

from relprim import (
    FallbackCandidate,
    FallbackChain,
    FallbackChainError,
    FallbackPolicy,
    fallback_chain,
)


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


async def test_fallback_chain_returns_primary_result_when_primary_succeeds() -> None:
    async def primary(value: str) -> str:
        return f"primary:{value}"

    async def secondary(value: str) -> str:
        return f"secondary:{value}"

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
    )

    result = await chain.run("input")

    assert result.value == "primary:input"
    assert result.candidate_name == "primary"
    assert result.candidate_index == 0
    assert result.fallback_used is False
    assert result.failure_count == 0
    assert result.failures == ()


async def test_fallback_chain_uses_next_candidate_when_primary_fails() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def secondary() -> str:
        return "secondary-result"

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
        policy=FallbackPolicy(fallback_on=(TransientError,)),
    )

    result = await chain.run()

    assert result.value == "secondary-result"
    assert result.candidate_name == "secondary"
    assert result.candidate_index == 1
    assert result.fallback_used is True
    assert result.failure_count == 1
    assert isinstance(result.failures[0], TransientError)


async def test_fallback_chain_passes_args_and_kwargs() -> None:
    async def primary(prefix: str, *, value: int) -> str:
        raise TransientError("primary unavailable")

    async def secondary(prefix: str, *, value: int) -> str:
        return f"{prefix}-{value}"

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
        policy=FallbackPolicy(fallback_on=(TransientError,)),
    )

    result = await chain.run("item", value=42)

    assert result.value == "item-42"


async def test_fallback_chain_tries_candidates_in_order() -> None:
    calls: list[str] = []

    async def primary() -> str:
        calls.append("primary")
        raise TransientError("primary unavailable")

    async def secondary() -> str:
        calls.append("secondary")
        raise TransientError("secondary unavailable")

    async def tertiary() -> str:
        calls.append("tertiary")
        return "tertiary-result"

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
        ("tertiary", tertiary),
        policy=FallbackPolicy(fallback_on=(TransientError,)),
    )

    result = await chain.run()

    assert result.value == "tertiary-result"
    assert result.candidate_name == "tertiary"
    assert result.candidate_index == 2
    assert result.fallback_used is True
    assert result.failure_count == 2
    assert calls == ["primary", "secondary", "tertiary"]


async def test_fallback_chain_raises_when_all_candidates_fail() -> None:
    async def primary() -> str:
        raise TransientError("primary unavailable")

    async def secondary() -> str:
        raise TransientError("secondary unavailable")

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
        policy=FallbackPolicy(fallback_on=(TransientError,)),
    )

    with pytest.raises(FallbackChainError) as exc_info:
        await chain.run()

    error = exc_info.value

    assert len(error.failures) == 2
    assert isinstance(error.cause, TransientError)
    assert str(error) == "All 2 fallback candidate(s) failed."


async def test_fallback_chain_propagates_non_fallbackable_exception() -> None:
    async def primary() -> str:
        raise PermanentError("do not fallback")

    async def secondary() -> str:
        return "secondary-result"

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
        policy=FallbackPolicy(fallback_on=(TransientError,)),
    )

    with pytest.raises(PermanentError):
        await chain.run()


async def test_fallback_chain_does_not_call_later_candidates_after_success() -> None:
    calls: list[str] = []

    async def primary() -> str:
        calls.append("primary")
        return "primary-result"

    async def secondary() -> str:
        calls.append("secondary")
        return "secondary-result"

    chain = fallback_chain(
        ("primary", primary),
        ("secondary", secondary),
    )

    result = await chain.run()

    assert result.value == "primary-result"
    assert calls == ["primary"]


def test_fallback_candidate_rejects_empty_name() -> None:
    async def operation() -> str:
        return "ok"

    with pytest.raises(ValueError):
        FallbackCandidate(name=" ", operation=operation)


def test_fallback_policy_requires_at_least_one_exception_type() -> None:
    with pytest.raises(ValueError):
        FallbackPolicy(fallback_on=())


def test_fallback_chain_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValueError):
        FallbackChain(candidates=())


async def test_fallback_chain_can_be_created_from_candidates_directly() -> None:
    async def primary() -> str:
        return "ok"

    chain = FallbackChain(candidates=(FallbackCandidate(name="primary", operation=primary),))

    result = await chain.run()

    assert result.value == "ok"
    assert result.candidate_name == "primary"
