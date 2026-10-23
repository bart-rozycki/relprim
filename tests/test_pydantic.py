from __future__ import annotations

import pytest
from pydantic import BaseModel

from relprim import (
    EventEmitter,
    EventType,
    InMemoryEventSink,
    OperationExecutionError,
    RetryPolicy,
    ValidationFailedError,
    Validator,
    async_operation,
    resilient,
    validation_policy,
)
from relprim.pydantic import PydanticValidator, pydantic_validator


class User(BaseModel):
    email: str


class ProviderResponse(BaseModel):
    id: int
    name: str
    user: User | None = None


async def no_sleep(delay: float) -> None:
    return None


def test_pydantic_validator_accepts_valid_mapping() -> None:
    validator = pydantic_validator(ProviderResponse)

    result = validator.validate({"id": 1, "name": "Ada"})

    assert result.valid is True
    assert result.reason is None


def test_pydantic_validator_rejects_invalid_mapping() -> None:
    validator = pydantic_validator(ProviderResponse)

    result = validator.validate({"id": "abc", "name": "Ada"})

    assert result.valid is False
    assert result.reason == (
        "Input should be a valid integer, unable to parse string as an integer "
        "(path: $.id, type: int_parsing)"
    )


def test_pydantic_validator_uses_model_name_by_default() -> None:
    validator = pydantic_validator(ProviderResponse)

    assert validator.name == "ProviderResponse"
    assert validator.validate({}).validator_name == "ProviderResponse"


def test_pydantic_validator_uses_custom_name() -> None:
    validator = pydantic_validator(ProviderResponse, name="provider_response")

    assert validator.validate({}).validator_name == "provider_response"


def test_pydantic_validator_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        pydantic_validator(ProviderResponse, name=" ")


def test_pydantic_validator_reports_nested_location() -> None:
    validator = pydantic_validator(ProviderResponse)

    result = validator.validate({"id": 1, "name": "Ada", "user": {}})

    assert result.reason == "Field required (path: $.user.email, type: missing)"


def test_pydantic_validator_reports_root_location_for_wrong_input_type() -> None:
    validator = pydantic_validator(ProviderResponse)

    result = validator.validate(["not", "a", "mapping"])

    assert result.reason == (
        "Input should be a valid dictionary or instance of ProviderResponse "
        "(path: $, type: model_type)"
    )


def test_pydantic_validator_reports_first_error_and_remaining_count() -> None:
    validator = pydantic_validator(ProviderResponse)

    one_more = validator.validate({"id": "abc"})
    two_more = validator.validate({"id": "abc", "user": {}})

    assert one_more.reason == (
        "Input should be a valid integer, unable to parse string as an integer "
        "(path: $.id, type: int_parsing) (+1 more validation error)"
    )
    assert two_more.reason == (
        "Input should be a valid integer, unable to parse string as an integer "
        "(path: $.id, type: int_parsing) (+2 more validation errors)"
    )


def test_pydantic_validator_does_not_leak_input_or_urls() -> None:
    validator = pydantic_validator(ProviderResponse)

    result = validator.validate({"id": "secret-token-value", "name": "Ada"})

    assert result.reason is not None
    assert "secret-token-value" not in result.reason
    assert "errors.pydantic.dev" not in result.reason


def test_pydantic_validator_uses_normal_coercion_by_default() -> None:
    validator = pydantic_validator(ProviderResponse)
    value = {"id": "1", "name": "Ada"}

    result = validator.validate(value)

    assert result.valid is True
    assert value == {"id": "1", "name": "Ada"}


def test_pydantic_validator_supports_strict_mode() -> None:
    validator = pydantic_validator(ProviderResponse, strict=True)

    result = validator.validate({"id": "1", "name": "Ada"})

    assert result.valid is False
    assert result.reason == "Input should be a valid integer (path: $.id, type: int_type)"
    assert validator.validate({"id": 1, "name": "Ada"}).valid is True


def test_pydantic_validator_satisfies_validation_policy() -> None:
    validator = pydantic_validator(ProviderResponse)
    policy = validation_policy(validator)

    assert isinstance(validator, PydanticValidator)
    assert isinstance(validator, Validator)
    assert policy.validate({"id": 1, "name": "Ada"}).valid is True

    with pytest.raises(ValidationFailedError) as exc_info:
        policy.validate_or_raise({"id": 1})

    assert exc_info.value.validator_name == "ProviderResponse"
    assert exc_info.value.reason == "Field required (path: $.name, type: missing)"


async def test_decorated_operation_accepts_valid_response_without_replacing_it() -> None:
    @resilient(
        name="provider_call",
        validation=validation_policy(pydantic_validator(ProviderResponse)),
    )
    async def provider() -> dict[str, object]:
        return {"id": "1", "name": "Ada"}

    result = await provider()

    assert result.value == {"id": "1", "name": "Ada"}
    assert result.report.metadata["validation_valid"] is True


async def test_decorated_operation_reports_invalid_response() -> None:
    sink = InMemoryEventSink()

    @resilient(
        name="provider_call",
        events=EventEmitter(sinks=(sink,)),
        validation=validation_policy(pydantic_validator(ProviderResponse)),
    )
    async def provider() -> dict[str, object]:
        return {"id": 1}

    with pytest.raises(OperationExecutionError) as exc_info:
        await provider()

    error = exc_info.value
    events = await sink.events()

    assert isinstance(error.cause, ValidationFailedError)
    assert error.report.metadata["validation_valid"] is False
    assert error.report.metadata["validation_reason"] == (
        "Field required (path: $.name, type: missing)"
    )
    assert EventType.VALIDATION_FAILED in [event.event_type for event in events]


async def test_retry_recovers_from_pydantic_validation_failure() -> None:
    calls = 0

    async def provider() -> dict[str, object]:
        nonlocal calls
        calls += 1

        if calls == 1:
            return {"id": 1}

        return {"id": 1, "name": "Ada"}

    result = await (
        async_operation("provider_call", provider)
        .with_retry(
            RetryPolicy(
                max_attempts=2,
                retry_on=(ValidationFailedError,),
                async_sleeper=no_sleep,
            )
        )
        .with_validation(validation_policy(pydantic_validator(ProviderResponse)))
        .run()
    )

    assert result.value == {"id": 1, "name": "Ada"}
    assert calls == 2
    assert result.report.attempt_count == 2


async def test_fallback_recovers_after_pydantic_validation_failure() -> None:
    async def backup() -> dict[str, object]:
        return {"id": 2, "name": "Backup"}

    @resilient(
        name="provider_call",
        retry=RetryPolicy(max_attempts=1, async_sleeper=no_sleep),
        fallback=backup,
        validation=validation_policy(pydantic_validator(ProviderResponse)),
    )
    async def provider() -> dict[str, object]:
        return {"name": "Primary"}

    result = await provider()

    assert result.value == {"id": 2, "name": "Backup"}
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["validation_valid"] is True
