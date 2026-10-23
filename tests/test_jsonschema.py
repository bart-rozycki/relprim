from __future__ import annotations

import pytest
from jsonschema.exceptions import SchemaError

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
from relprim.jsonschema import JSONSchemaValidator, json_schema_validator

PROVIDER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["id", "name"],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "user": {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    },
}


async def no_sleep(delay: float) -> None:
    return None


def test_json_schema_validator_accepts_valid_value() -> None:
    validator = json_schema_validator(PROVIDER_RESPONSE_SCHEMA)

    result = validator.validate({"id": 1, "name": "Ada"})

    assert result.valid is True
    assert result.reason is None


def test_json_schema_validator_rejects_invalid_value() -> None:
    validator = json_schema_validator(PROVIDER_RESPONSE_SCHEMA)

    result = validator.validate({"id": 1, "name": 123})

    assert result.valid is False
    assert result.reason == "123 is not of type 'string' (path: $.name)"


def test_json_schema_validator_uses_default_name() -> None:
    validator = json_schema_validator(PROVIDER_RESPONSE_SCHEMA)

    assert validator.name == "json_schema"
    assert validator.validate({}).validator_name == "json_schema"


def test_json_schema_validator_uses_custom_name() -> None:
    validator = json_schema_validator(
        PROVIDER_RESPONSE_SCHEMA,
        name="provider_response",
    )

    assert validator.validate({}).validator_name == "provider_response"


def test_json_schema_validator_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        json_schema_validator(PROVIDER_RESPONSE_SCHEMA, name=" ")


def test_json_schema_validator_reports_required_property_at_root() -> None:
    validator = json_schema_validator(PROVIDER_RESPONSE_SCHEMA)

    result = validator.validate({"id": 1})

    assert result.reason == "'name' is a required property (path: $)"


def test_json_schema_validator_reports_nested_path() -> None:
    validator = json_schema_validator(PROVIDER_RESPONSE_SCHEMA)

    result = validator.validate(
        {
            "id": 1,
            "name": "Ada",
            "user": {"tags": ["admin", 7]},
        }
    )

    assert result.reason == "7 is not of type 'string' (path: $.user.tags[1])"


def test_json_schema_validator_truncates_long_messages() -> None:
    validator = json_schema_validator({"type": "string"})

    result = validator.validate(list(range(1_000)))

    assert result.reason is not None
    assert len(result.reason) < 250
    assert result.reason.endswith("... (path: $)")


def test_json_schema_validator_does_not_mutate_value() -> None:
    validator = json_schema_validator(
        {
            "type": "object",
            "properties": {"name": {"type": "string", "default": "unknown"}},
        }
    )
    value: dict[str, object] = {}

    result = validator.validate(value)

    assert result.valid is True
    assert value == {}


def test_json_schema_validator_rejects_invalid_schema_at_construction() -> None:
    with pytest.raises(SchemaError):
        json_schema_validator({"type": "not-a-real-type"})


def test_json_schema_validator_respects_declared_draft() -> None:
    # exclusiveMinimum is a boolean modifier in draft 4 and a number in draft 6+.
    draft4 = json_schema_validator(
        {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "integer",
            "minimum": 10,
            "exclusiveMinimum": True,
        }
    )
    draft2020 = json_schema_validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "integer",
            "exclusiveMinimum": 10,
        }
    )

    assert draft4.validate(10).valid is False
    assert draft4.validate(11).valid is True
    assert draft2020.validate(10).valid is False
    assert draft2020.validate(11).valid is True

    with pytest.raises(SchemaError):
        json_schema_validator(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "exclusiveMinimum": True,
            }
        )


def test_json_schema_validator_satisfies_validation_policy() -> None:
    validator = json_schema_validator(PROVIDER_RESPONSE_SCHEMA, name="provider_response")
    policy = validation_policy(validator)

    assert isinstance(validator, JSONSchemaValidator)
    assert isinstance(validator, Validator)
    assert policy.validate({"id": 1, "name": "Ada"}).valid is True

    with pytest.raises(ValidationFailedError) as exc_info:
        policy.validate_or_raise({"id": "1", "name": "Ada"})

    assert exc_info.value.validator_name == "provider_response"
    assert exc_info.value.reason == "'1' is not of type 'integer' (path: $.id)"


async def test_decorated_operation_accepts_valid_response() -> None:
    @resilient(
        name="provider_call",
        validation=validation_policy(
            json_schema_validator(PROVIDER_RESPONSE_SCHEMA, name="provider_response")
        ),
    )
    async def provider() -> dict[str, object]:
        return {"id": 1, "name": "Ada"}

    result = await provider()

    assert result.value == {"id": 1, "name": "Ada"}
    assert result.report.metadata["validation_valid"] is True


async def test_decorated_operation_reports_invalid_response() -> None:
    sink = InMemoryEventSink()

    @resilient(
        name="provider_call",
        events=EventEmitter(sinks=(sink,)),
        validation=validation_policy(
            json_schema_validator(PROVIDER_RESPONSE_SCHEMA, name="provider_response")
        ),
    )
    async def provider() -> dict[str, object]:
        return {"id": 1}

    with pytest.raises(OperationExecutionError) as exc_info:
        await provider()

    error = exc_info.value
    events = await sink.events()

    assert isinstance(error.cause, ValidationFailedError)
    assert error.report.metadata["validation_valid"] is False
    assert error.report.metadata["validation_validator_name"] == "provider_response"
    assert error.report.metadata["validation_reason"] == ("'name' is a required property (path: $)")
    assert EventType.VALIDATION_FAILED in [event.event_type for event in events]


async def test_retry_recovers_from_json_schema_validation_failure() -> None:
    calls = 0

    async def provider() -> dict[str, object]:
        nonlocal calls
        calls += 1

        if calls == 1:
            return {"id": "not-an-integer", "name": "Ada"}

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
        .with_validation(validation_policy(json_schema_validator(PROVIDER_RESPONSE_SCHEMA)))
        .run()
    )

    assert result.value == {"id": 1, "name": "Ada"}
    assert calls == 2
    assert result.report.attempt_count == 2


async def test_fallback_recovers_after_json_schema_validation_failure() -> None:
    async def backup() -> dict[str, object]:
        return {"id": 2, "name": "Backup"}

    @resilient(
        name="provider_call",
        retry=RetryPolicy(max_attempts=1, async_sleeper=no_sleep),
        fallback=backup,
        validation=validation_policy(json_schema_validator(PROVIDER_RESPONSE_SCHEMA)),
    )
    async def provider() -> dict[str, object]:
        return {"name": "Primary"}

    result = await provider()

    assert result.value == {"id": 2, "name": "Backup"}
    assert result.report.metadata["fallback_used"] is True
    assert result.report.metadata["validation_valid"] is True
