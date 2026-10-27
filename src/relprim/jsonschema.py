from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

try:
    from jsonschema.exceptions import ValidationError, best_match
    from jsonschema.protocols import Validator as SchemaValidator
    from jsonschema.validators import validator_for
except ModuleNotFoundError as exc:
    if exc.name is None or exc.name.split(".")[0] != "jsonschema":
        raise

    raise ModuleNotFoundError(
        "relprim.jsonschema requires the optional 'jsonschema' package. "
        "Install it with: pip install 'relprim[jsonschema]'",
        name=exc.name,
    ) from exc

from relprim.validation import ValidationResult

DEFAULT_VALIDATOR_NAME = "json_schema"
MAX_MESSAGE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class JSONSchemaValidator:
    """Validator backed by a JSON Schema.

    The validator class is selected from the schema's ``$schema`` keyword using
    jsonschema's native discovery. Schemas without ``$schema`` use the latest
    draft supported by the installed jsonschema version.

    The schema is checked against its metaschema at construction time, so an
    invalid schema raises ``jsonschema.exceptions.SchemaError`` immediately
    instead of failing every validated value.

    Values of any type are accepted. Values that are not JSON-compatible are
    reported as invalid by the schema's type checks rather than raising.

    Validated values are never mutated or converted. When a value is invalid,
    the most relevant error selected by ``jsonschema.exceptions.best_match`` is
    reported as the failure reason.
    """

    schema: Mapping[str, Any]
    name: str
    _schema_validator: SchemaValidator = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty.")

        validator_class = validator_for(self.schema)
        validator_class.check_schema(dict(self.schema))
        object.__setattr__(self, "_schema_validator", validator_class(self.schema))

    def validate(self, value: object) -> ValidationResult:
        # jsonschema's stubs describe JSON inputs, but any value is accepted at
        # runtime and non-JSON values fail the schema's type checks.
        instance = cast(Any, value)
        error: ValidationError | None = best_match(self._schema_validator.iter_errors(instance))

        if error is None:
            return ValidationResult.success(validator_name=self.name)

        return ValidationResult.failure(
            validator_name=self.name,
            reason=_format_error(error),
        )


def json_schema_validator(
    schema: Mapping[str, Any],
    *,
    name: str = DEFAULT_VALIDATOR_NAME,
) -> JSONSchemaValidator:
    """Create a JSON Schema validator."""
    return JSONSchemaValidator(schema=schema, name=name)


def _format_error(error: ValidationError) -> str:
    message = error.message

    if len(message) > MAX_MESSAGE_LENGTH:
        message = f"{message[: MAX_MESSAGE_LENGTH - 3]}..."

    return f"{message} (path: {_format_path(error.absolute_path)})"


def _format_path(path: Iterable[str | int]) -> str:
    formatted = "$"

    for part in path:
        formatted += f"[{part}]" if isinstance(part, int) else f".{part}"

    return formatted


__all__ = [
    "JSONSchemaValidator",
    "json_schema_validator",
]
