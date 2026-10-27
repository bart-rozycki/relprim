"""Downstream typing contract for @resilient(...).

Checked with `mypy --strict` by tests/test_typing.py. `assert_type` pins the
inferred types; `# type: ignore[code]` marks expected errors, and strict mode's
warn_unused_ignores fails if an expected error disappears.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Never, assert_type

from pydantic import BaseModel

from relprim import (
    CircuitBreaker,
    EventEmitter,
    InMemoryEventSink,
    InMemoryIdempotencyStore,
    OperationResult,
    RateLimitPolicy,
    ResilientDecorator,
    RetryPolicy,
    TimeoutPolicy,
    fallback_chain,
    idempotency_policy,
    resilient,
    validation_policy,
    validator,
)
from relprim.jsonschema import json_schema_validator
from relprim.pydantic import pydantic_validator


class User:
    pass


class AdminUser(User):
    pass


class UserModel(BaseModel):
    id: int


class RateLimited(Exception):
    pass


LoadUser = Callable[[int, bool], Coroutine[Any, Any, OperationResult[User]]]


@resilient()
async def no_policy(user_id: int, active: bool = True) -> User:
    return User()


no_policy_alias: LoadUser = no_policy


@resilient(retries=3)
async def with_retries(user_id: int, active: bool = True) -> User:
    return User()


with_retries_alias: LoadUser = with_retries


@resilient(retries=2, retry_on=(ValueError,), timeout=10)
async def with_timeout(user_id: int, active: bool = True) -> User:
    return User()


@resilient(timeout=TimeoutPolicy(seconds=5), retry=RetryPolicy(max_attempts=2))
async def with_policies(user_id: int, *, active: bool = True) -> User:
    return User()


@resilient(validation=validation_policy(validator("x", lambda value: True, message="m")))
async def with_callable_validation(user_id: int) -> User:
    return User()


@resilient(validation=validation_policy(json_schema_validator({"type": "object"})))
async def with_json_schema(user_id: int) -> dict[str, object]:
    return {}


@resilient(validation=validation_policy(pydantic_validator(UserModel)))
async def with_pydantic(user_id: int) -> dict[str, object]:
    return {}


@resilient(
    idempotency_key=lambda request_id, amount: request_id,
    idempotency_store=InMemoryIdempotencyStore(),
    idempotency_ttl=60,
)
async def with_idempotency_key(request_id: str, amount: int) -> str:
    return request_id


def payment_key(request_id: str, amount: int) -> str:
    return request_id


@resilient(idempotency=idempotency_policy(payment_key, ttl_seconds=60))
async def with_idempotency_policy(request_id: str, amount: int) -> str:
    return request_id


@resilient(retries=2, rate_limit_on=(RateLimited,), retry_after=lambda e: 1.0)
async def with_rate_limit(prompt: str) -> str:
    return prompt


@resilient(
    retries=2,
    rate_limit=RateLimitPolicy(rate_limit_on=(RateLimited,), max_wait_seconds=5),
    circuit_breaker=CircuitBreaker(),
    events=EventEmitter(sinks=(InMemoryEventSink(),)),
)
async def with_advanced(prompt: str) -> str:
    return prompt


async def backup_user(user_id: int, active: bool = True) -> User:
    return User()


async def backup_admin(user_id: int, active: bool = True) -> AdminUser:
    return AdminUser()


async def backup_none(user_id: int, active: bool = True) -> None:
    return None


@resilient(fallback=backup_user)
async def with_fallback(user_id: int, active: bool = True) -> User:
    return User()


@resilient(fallback=backup_admin)
async def with_subtype_fallback(user_id: int, active: bool = True) -> User:
    return User()


@resilient(fallbacks=fallback_chain(("backup", backup_none)))
async def with_optional_fallback(user_id: int, active: bool = True) -> User:
    return User()


@resilient(
    retries=3,
    timeout=10,
    fallback=backup_user,
    validation=validation_policy(pydantic_validator(UserModel)),
    idempotency_key=lambda user_id, active=True: str(user_id),
    rate_limit_on=(RateLimited,),
    retry_after=lambda e: None,
    max_rate_limit_wait=30,
)
async def with_everything(user_id: int, active: bool = True) -> User:
    return User()


retrying = resilient(retries=3)
assert_type(retrying, ResilientDecorator[Never])


@retrying
async def reused(user_id: int) -> int:
    return user_id


async def callers() -> None:
    assert_type(await no_policy(1), OperationResult[User])
    assert_type(await with_retries(1, active=False), OperationResult[User])
    assert_type(await with_timeout(1), OperationResult[User])
    assert_type(await with_policies(1, active=False), OperationResult[User])
    assert_type(await with_callable_validation(1), OperationResult[User])
    assert_type(await with_json_schema(1), OperationResult[dict[str, object]])
    assert_type(await with_pydantic(1), OperationResult[dict[str, object]])
    assert_type(await with_idempotency_key("r", 1), OperationResult[str])
    assert_type(await with_idempotency_policy("r", amount=1), OperationResult[str])
    assert_type(await with_rate_limit("hi"), OperationResult[str])
    assert_type(await with_advanced("hi"), OperationResult[str])
    assert_type(await with_fallback(1), OperationResult[User])
    assert_type(await with_subtype_fallback(1), OperationResult[User | AdminUser])
    assert_type(await with_optional_fallback(1), OperationResult[User | None])
    assert_type(await with_everything(1, True), OperationResult[User])
    assert_type(await reused(1), OperationResult[int])

    result = await with_retries(1)
    user: User = result.value
    assert_type(user, User)

    # Decorated functions return real coroutines usable with asyncio tasks.
    task = asyncio.create_task(with_retries(1))
    assert_type(await task, OperationResult[User])

    # The original signature is enforced.
    await with_retries("1")  # type: ignore[arg-type]
    # Keyword-only parameters stay keyword-only. Checked by assignment because
    # mypy versions disagree on the error code of a direct call.
    positional: Callable[[int, bool], Coroutine[Any, Any, OperationResult[User]]]
    positional = with_policies  # type: ignore[assignment]  # noqa: F841
    await with_retries(1, active=True, extra=1)  # type: ignore[call-arg]
    await with_fallback()  # type: ignore[call-arg]
    wrong: OperationResult[str] = await with_retries(1)  # type: ignore[assignment]  # noqa: F841
