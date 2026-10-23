# JSON Schema validation

RelPrim can validate operation results against a JSON Schema.

The integration is provided by `JSONSchemaValidator`. It implements RelPrim's
`Validator` protocol, so it works anywhere a validator already works:
`validation_policy(...)`, `@resilient(validation=...)` and
`AsyncOperation.with_validation(...)`.

Schema semantics are provided by the
[`jsonschema`](https://python-jsonschema.readthedocs.io/) package. RelPrim only
translates its results into `ValidationResult` objects.

## Install

Install RelPrim with the optional JSON Schema dependency:

```bash
pip install "relprim[jsonschema]"
```

## Create a validator

```python
from relprim import validation_policy
from relprim.jsonschema import json_schema_validator


PROVIDER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["id", "name"],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
    },
}

response_validator = json_schema_validator(
    PROVIDER_RESPONSE_SCHEMA,
    name="provider_response",
)

response_validation = validation_policy(response_validator)
```

`name` defaults to `"json_schema"`. Blank names are rejected.

The validator can also be called directly:

```python
result = response_validator.validate({"id": 1})

print(result.valid)
print(result.reason)
```

```text
False
'name' is a required property (path: $)
```

## Decorator usage

```python
from relprim import resilient, validation_policy
from relprim.jsonschema import json_schema_validator


@resilient(
    retries=2,
    timeout=10,
    validation=validation_policy(
        json_schema_validator(PROVIDER_RESPONSE_SCHEMA, name="provider_response")
    ),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

## Builder usage

```python
from relprim import async_operation, validation_policy
from relprim.jsonschema import json_schema_validator


result = await (
    async_operation("fetch_user", fetch_user)
    .with_validation(
        validation_policy(json_schema_validator(PROVIDER_RESPONSE_SCHEMA, name="provider_response"))
    )
    .run(42)
)
```

## Schema drafts

The draft is selected from the schema's `$schema` keyword using jsonschema's
own validator discovery:

```python
json_schema_validator(
    {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
    }
)
```

Schemas without `$schema` use the latest draft supported by the installed
jsonschema version.

## Invalid schemas

The schema is checked against its metaschema when the validator is created.

An invalid schema raises `jsonschema.exceptions.SchemaError` immediately:

```python
json_schema_validator({"type": "not-a-real-type"})  # raises SchemaError
```

Configuration errors are never reported as invalid operation results.

## Validation failures

When a value does not match the schema, the validator returns a failed
`ValidationResult`. RelPrim then raises `ValidationFailedError` for that
attempt through its normal validation machinery.

The reason contains the jsonschema error message and the location of the
invalid value:

```text
'name' is a required property (path: $)
123 is not of type 'string' (path: $.name)
7 is not of type 'string' (path: $.user.tags[1])
```

Paths start at `$` (the whole value). Object properties are written as
`.name` and array indexes as `[1]`.

When a value has several errors, the single most relevant error selected by
`jsonschema.exceptions.best_match` is reported. The selection is deterministic
for the same schema and value.

Messages longer than 200 characters are truncated, so large invalid values are
not copied into reports and events.

## Retry and fallback

JSON Schema failures are ordinary `ValidationFailedError` failures.

Retry them explicitly:

```python
from relprim import ValidationFailedError, resilient


@resilient(
    retries=2,
    retry_on=(ValidationFailedError,),
    validation=validation_policy(json_schema_validator(PROVIDER_RESPONSE_SCHEMA)),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

Fallback results are validated with the same policy. A fallback can recover
from a primary result that failed validation:

```python
@resilient(
    fallback=fetch_user_from_backup,
    validation=validation_policy(json_schema_validator(PROVIDER_RESPONSE_SCHEMA)),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

Validator names and failure reasons appear in execution report metadata and
structured validation events.

## Limitations

* Values are validated as they are. They are never converted, mutated or
  filled with schema defaults.
* Only the most relevant error is reported.
* `format` is treated as an annotation, following jsonschema's default
  behavior. Format assertions are not enabled.
* Remote `$ref` resolution is not configured by RelPrim.
* Values that are not JSON-compatible, such as dataclass instances, are
  reported as invalid by the schema's type checks.

## Dependency model

`jsonschema` is an optional dependency.

`import relprim` never imports it. Only `relprim.jsonschema` requires it, so
the core package remains dependency-free.

See [`examples/json_schema_validation.py`](../examples/json_schema_validation.py)
for a runnable example.
