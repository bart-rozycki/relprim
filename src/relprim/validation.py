from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from relprim.errors import ValidationFailedError

T = TypeVar("T")
T_contra = TypeVar("T_contra", contravariant=True)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result returned by a validator.

    ValidationResult is intentionally small and serializable. It can later be
    attached to execution reports, structured events and persistent stores.
    """

    valid: bool
    validator_name: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.validator_name.strip():
            raise ValueError("validator_name must not be empty.")

        if not self.valid and not self.reason:
            raise ValueError("invalid validation results must include a reason.")

        if self.valid and self.reason is not None:
            raise ValueError("valid validation results must not include a reason.")

    @classmethod
    def success(cls, *, validator_name: str) -> ValidationResult:
        return cls(valid=True, validator_name=validator_name)

    @classmethod
    def failure(cls, *, validator_name: str, reason: str) -> ValidationResult:
        return cls(valid=False, validator_name=validator_name, reason=reason)

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "valid": self.valid,
            "validator_name": self.validator_name,
            "reason": self.reason,
        }


@runtime_checkable
class Validator(Protocol[T_contra]):
    """Protocol implemented by value validators."""

    name: str

    def validate(self, value: T_contra) -> ValidationResult:
        """Validate a value and return a structured validation result."""


@dataclass(frozen=True, slots=True)
class CallableValidator(Generic[T]):
    """Validator backed by a plain callable.

    The callable should return True when the value is acceptable and False when
    it is invalid.

    This intentionally keeps the first validation primitive dependency-free.
    JSON Schema, Pydantic and semantic validators can be added later as adapters.
    """

    name: str
    predicate: Callable[[T], bool]
    message: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")
        if not self.message.strip():
            raise ValueError("message must not be empty.")

    def validate(self, value: T) -> ValidationResult:
        if self.predicate(value):
            return ValidationResult.success(validator_name=self.name)

        return ValidationResult.failure(
            validator_name=self.name,
            reason=self.message,
        )


@dataclass(frozen=True, slots=True)
class ValidationPolicy(Generic[T]):
    """Runs one or more validators against a value.

    Validators are executed in order. The first failed validator stops execution
    and returns its failure result.

    This is fail-fast by design. It avoids doing extra validation work after the
    value is already known to be unacceptable.
    """

    validators: tuple[Validator[T], ...]

    def __post_init__(self) -> None:
        if not self.validators:
            raise ValueError("validators must contain at least one validator.")

    @classmethod
    def from_validators(cls, *validators: Validator[T]) -> ValidationPolicy[T]:
        return cls(validators=validators)

    def validate(self, value: T) -> ValidationResult:
        for validator in self.validators:
            result = validator.validate(value)

            if not result.valid:
                return result

        return ValidationResult.success(validator_name="validation_policy")

    def validate_or_raise(self, value: T) -> None:
        result = self.validate(value)

        if result.valid:
            return

        if result.reason is None:
            raise RuntimeError("Validation failed without a reason.")

        raise ValidationFailedError(
            f"Validation failed in '{result.validator_name}': {result.reason}",
            validator_name=result.validator_name,
            reason=result.reason,
        )


def validator(
    name: str,
    predicate: Callable[[T], bool],
    *,
    message: str,
) -> CallableValidator[T]:
    """Create a callable validator."""
    return CallableValidator(
        name=name,
        predicate=predicate,
        message=message,
    )


def validation_policy(*validators: Validator[T]) -> ValidationPolicy[T]:
    """Create a validation policy from validators."""
    return ValidationPolicy.from_validators(*validators)
