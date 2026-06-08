from __future__ import annotations

import pytest

from relprim import (
    CallableValidator,
    ValidationFailedError,
    ValidationPolicy,
    ValidationResult,
    validation_policy,
    validator,
)


def test_validation_result_success_factory() -> None:
    result = ValidationResult.success(validator_name="non_empty")

    assert result.valid is True
    assert result.validator_name == "non_empty"
    assert result.reason is None
    assert result.to_dict() == {
        "valid": True,
        "validator_name": "non_empty",
        "reason": None,
    }


def test_validation_result_failure_factory() -> None:
    result = ValidationResult.failure(
        validator_name="non_empty",
        reason="Value must not be empty.",
    )

    assert result.valid is False
    assert result.validator_name == "non_empty"
    assert result.reason == "Value must not be empty."
    assert result.to_dict() == {
        "valid": False,
        "validator_name": "non_empty",
        "reason": "Value must not be empty.",
    }


def test_validation_result_rejects_empty_validator_name() -> None:
    with pytest.raises(ValueError):
        ValidationResult.success(validator_name=" ")


def test_validation_result_requires_reason_for_failure() -> None:
    with pytest.raises(ValueError):
        ValidationResult(valid=False, validator_name="non_empty")


def test_validation_result_rejects_reason_for_success() -> None:
    with pytest.raises(ValueError):
        ValidationResult(
            valid=True,
            validator_name="non_empty",
            reason="should not exist",
        )


def test_callable_validator_returns_success_when_predicate_passes() -> None:
    non_empty = CallableValidator[str](
        name="non_empty",
        predicate=lambda value: bool(value.strip()),
        message="Value must not be empty.",
    )

    result = non_empty.validate("hello")

    assert result.valid is True
    assert result.validator_name == "non_empty"
    assert result.reason is None


def test_callable_validator_returns_failure_when_predicate_fails() -> None:
    non_empty = CallableValidator[str](
        name="non_empty",
        predicate=lambda value: bool(value.strip()),
        message="Value must not be empty.",
    )

    result = non_empty.validate(" ")

    assert result.valid is False
    assert result.validator_name == "non_empty"
    assert result.reason == "Value must not be empty."


def test_callable_validator_factory() -> None:
    non_empty = validator(
        "non_empty",
        lambda value: bool(value.strip()),
        message="Value must not be empty.",
    )

    result = non_empty.validate("hello")

    assert result.valid is True


def test_callable_validator_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        CallableValidator[str](
            name=" ",
            predicate=lambda value: True,
            message="Value must not be empty.",
        )


def test_callable_validator_rejects_empty_message() -> None:
    with pytest.raises(ValueError):
        CallableValidator[str](
            name="non_empty",
            predicate=lambda value: True,
            message=" ",
        )


def test_validation_policy_returns_success_when_all_validators_pass() -> None:
    non_empty = validator(
        "non_empty",
        lambda value: bool(value.strip()),
        message="Value must not be empty.",
    )
    short_enough = validator(
        "short_enough",
        lambda value: len(value) <= 20,
        message="Value must be at most 20 characters.",
    )

    policy = ValidationPolicy.from_validators(non_empty, short_enough)

    result = policy.validate("hello")

    assert result.valid is True
    assert result.validator_name == "validation_policy"
    assert result.reason is None


def test_validation_policy_returns_first_failure() -> None:
    non_empty = validator(
        "non_empty",
        lambda value: bool(value.strip()),
        message="Value must not be empty.",
    )
    short_enough = validator(
        "short_enough",
        lambda value: len(value) <= 20,
        message="Value must be at most 20 characters.",
    )

    policy = validation_policy(non_empty, short_enough)

    result = policy.validate(" ")

    assert result.valid is False
    assert result.validator_name == "non_empty"
    assert result.reason == "Value must not be empty."


def test_validation_policy_is_fail_fast() -> None:
    calls: list[str] = []

    first = validator(
        "first",
        lambda value: calls.append("first") is None and False,
        message="First failed.",
    )
    second = validator(
        "second",
        lambda value: calls.append("second") is None and True,
        message="Second failed.",
    )

    policy = validation_policy(first, second)

    result = policy.validate("value")

    assert result.valid is False
    assert calls == ["first"]


def test_validation_policy_rejects_empty_validator_list() -> None:
    with pytest.raises(ValueError):
        ValidationPolicy[str](validators=())


def test_validate_or_raise_does_not_raise_for_valid_value() -> None:
    policy = validation_policy(
        validator(
            "non_empty",
            lambda value: bool(value.strip()),
            message="Value must not be empty.",
        )
    )

    policy.validate_or_raise("hello")


def test_validate_or_raise_raises_validation_failed_error() -> None:
    policy = validation_policy(
        validator(
            "non_empty",
            lambda value: bool(value.strip()),
            message="Value must not be empty.",
        )
    )

    with pytest.raises(ValidationFailedError) as exc_info:
        policy.validate_or_raise(" ")

    error = exc_info.value

    assert error.validator_name == "non_empty"
    assert error.reason == "Value must not be empty."
    assert str(error) == "Validation failed in 'non_empty': Value must not be empty."


def test_validation_policy_accepts_custom_validator_object() -> None:
    class StartsWithPrefix:
        name = "starts_with_prefix"

        def validate(self, value: str) -> ValidationResult:
            if value.startswith("rel"):
                return ValidationResult.success(validator_name=self.name)

            return ValidationResult.failure(
                validator_name=self.name,
                reason="Value must start with 'rel'.",
            )

    policy = validation_policy(StartsWithPrefix())

    assert policy.validate("relprim").valid is True

    result = policy.validate("python")

    assert result.valid is False
    assert result.validator_name == "starts_with_prefix"
    assert result.reason == "Value must start with 'rel'."
