from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from relprim.validation import ValidationResult


@dataclass(frozen=True, slots=True)
class PydanticValidator:
    """Validator backed by a Pydantic v2 model.

    Values are validated with ``model.model_validate(...)`` using the model's
    normal validation semantics. The validated model instance is discarded:
    the operation result is never replaced or converted.

    ``strict`` is passed to ``model_validate``. The default ``None`` keeps the
    model's own configuration.

    When a value is invalid, the first Pydantic error is reported as the
    failure reason together with the number of remaining errors.
    """

    model: type[BaseModel]
    name: str
    strict: bool | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")

    def validate(self, value: object) -> ValidationResult:
        try:
            self.model.model_validate(value, strict=self.strict)
        except ValidationError as exc:
            return ValidationResult.failure(
                validator_name=self.name,
                reason=_format_error(exc),
            )

        return ValidationResult.success(validator_name=self.name)


def pydantic_validator(
    model: type[BaseModel],
    *,
    name: str | None = None,
    strict: bool | None = None,
) -> PydanticValidator:
    """Create a Pydantic validator.

    The validator name defaults to the model class name.
    """
    return PydanticValidator(
        model=model,
        name=model.__name__ if name is None else name,
        strict=strict,
    )


def _format_error(exception: ValidationError) -> str:
    errors = exception.errors(include_url=False)
    first = errors[0]
    reason = f"{first['msg']} (path: {_format_path(first['loc'])}, type: {first['type']})"

    remaining = len(errors) - 1

    if remaining == 1:
        reason += " (+1 more validation error)"
    elif remaining > 1:
        reason += f" (+{remaining} more validation errors)"

    return reason


def _format_path(path: Iterable[str | int]) -> str:
    formatted = "$"

    for part in path:
        formatted += f"[{part}]" if isinstance(part, int) else f".{part}"

    return formatted


__all__ = [
    "PydanticValidator",
    "pydantic_validator",
]
