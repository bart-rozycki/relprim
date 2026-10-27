"""Downstream typing contract for validation, the builder API and public protocols.

Checked with `mypy --strict` by tests/test_typing.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar, assert_type

from pydantic import BaseModel

from relprim import (
    AsyncOperation,
    CallableValidator,
    EventEmitter,
    EventSink,
    FallbackChain,
    IdempotencyResult,
    IdempotencyStatus,
    IdempotencyStore,
    OperationResult,
    StructuredEvent,
    ValidationPolicy,
    ValidationResult,
    Validator,
    async_operation,
    fallback_chain,
    idempotency_policy,
    validation_policy,
    validator,
)
from relprim.jsonschema import JSONSchemaValidator, json_schema_validator
from relprim.pydantic import PydanticValidator, pydantic_validator

R = TypeVar("R")


class UserModel(BaseModel):
    id: int


class User:
    pass


class AdminUser(User):
    pass


# Intermediate variables keep precise types instead of degrading to Any.
schema_policy = validation_policy(json_schema_validator({"type": "object"}))
assert_type(schema_policy, ValidationPolicy[object])

model_policy = validation_policy(pydantic_validator(UserModel))
assert_type(model_policy, ValidationPolicy[object])

assert_type(json_schema_validator({}), JSONSchemaValidator)
assert_type(pydantic_validator(UserModel), PydanticValidator)


def is_positive(value: int) -> bool:
    return value > 0


positive = validator("positive", is_positive, message="must be positive")
assert_type(positive, CallableValidator[int])

int_policy = validation_policy(positive)
assert_type(int_policy, ValidationPolicy[int])

# Validators accepting a wider type combine with narrower ones.
combined = validation_policy(positive, json_schema_validator({"type": "integer"}))
assert_type(combined, ValidationPolicy[int])

# ValidationPolicy is contravariant: a policy for `object` fits any result type.
dict_policy: ValidationPolicy[dict[str, object]] = schema_policy
bool_policy: ValidationPolicy[bool] = int_policy
str_policy: ValidationPolicy[str] = int_policy  # type: ignore[assignment]

assert_type(ValidationPolicy.from_validators(positive), ValidationPolicy[int])


class LengthValidator:
    """A custom validator satisfies the Validator protocol structurally."""

    name = "length"

    def validate(self, value: str) -> ValidationResult:
        return ValidationResult.success(validator_name=self.name)


length_validator: Validator[str] = LengthValidator()
assert_type(validation_policy(LengthValidator()), ValidationPolicy[str])


async def fetch_user(user_id: int) -> dict[str, object]:
    return {"id": user_id}


async def fetch_count(user_id: int) -> int:
    return user_id


operation = async_operation("fetch_user", fetch_user)
operation_check: AsyncOperation[[int], dict[str, object]] = operation
wrong_parameters: AsyncOperation[[str], dict[str, object]] = operation  # type: ignore[assignment]

validated = operation.with_validation(schema_policy).with_validation(model_policy)
validated_check: AsyncOperation[[int], dict[str, object]] = validated

async_operation("fetch_count", fetch_count).with_validation(int_policy)
async_operation("fetch_user", fetch_user).with_validation(int_policy)  # type: ignore[arg-type]


async def load_user(user_id: int) -> User:
    return User()


async def load_admin(user_id: int) -> AdminUser:
    return AdminUser()


admin_chain = fallback_chain(("admin", load_admin))
admin_chain_check: FallbackChain[[int], AdminUser] = admin_chain

# A chain producing a subtype fits an operation producing the base type.
user_operation = async_operation("load_user", load_user).with_fallbacks(admin_chain)
user_operation_check: AsyncOperation[[int], User] = user_operation

# OperationResult is covariant in its value type.
admin_result: OperationResult[AdminUser] | None = None
user_result: OperationResult[User] | None = admin_result

keyed = async_operation("fetch_user", fetch_user).with_idempotency(
    idempotency_policy(lambda user_id: f"user:{user_id}")
)
keyed_check: AsyncOperation[[int], dict[str, object]] = keyed


async def builder_callers() -> None:
    assert_type(await validated.run(1), OperationResult[dict[str, object]])
    assert_type(await user_operation.run(1), OperationResult[User])
    await validated.run("1")  # type: ignore[arg-type]


class ListSink:
    """A custom sink satisfies the EventSink protocol structurally."""

    def __init__(self) -> None:
        self.events: list[StructuredEvent] = []

    async def emit(self, event: StructuredEvent) -> None:
        self.events.append(event)


EventEmitter(sinks=(ListSink(),))
sink: EventSink = ListSink()


class PassthroughStore:
    """A custom store satisfies the IdempotencyStore protocol structurally."""

    async def execute(
        self,
        key: str,
        operation: Callable[[], Awaitable[R]],
        *,
        ttl_seconds: float,
    ) -> IdempotencyResult[R]:
        return IdempotencyResult(
            value=await operation(),
            key=key,
            status=IdempotencyStatus.EXECUTED,
        )


store: IdempotencyStore = PassthroughStore()
