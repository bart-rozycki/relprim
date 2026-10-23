# Pydantic validation

RelPrim can validate operation results against a Pydantic model.

The integration is provided by `PydanticValidator`. It implements RelPrim's
`Validator` protocol, so it works anywhere a validator already works:
`validation_policy(...)`, `@resilient(validation=...)` and
`AsyncOperation.with_validation(...)`.

Validation semantics are provided by Pydantic v2. RelPrim only translates its
results into `ValidationResult` objects.

## Install

Install RelPrim with the optional Pydantic dependency:

```bash
pip install "relprim[pydantic]"
```

## Create a validator

```python
from pydantic import BaseModel

from relprim import validation_policy
from relprim.pydantic import pydantic_validator


class ProviderResponse(BaseModel):
    id: int
    name: str


response_validator = pydantic_validator(ProviderResponse)

response_validation = validation_policy(response_validator)
```

`name` defaults to the model class name, here `"ProviderResponse"`. Pass
`name=...` to use a different name. Blank names are rejected.

The validator can also be called directly:

```python
result = response_validator.validate({"id": "abc", "name": "Ada"})

print(result.valid)
print(result.reason)
```

```text
False
Input should be a valid integer, unable to parse string as an integer (path: $.id, type: int_parsing)
```

## Decorator usage

```python
from relprim import resilient, validation_policy
from relprim.pydantic import pydantic_validator


@resilient(
    retries=2,
    timeout=10,
    validation=validation_policy(pydantic_validator(ProviderResponse)),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

## Builder usage

```python
from relprim import async_operation, validation_policy
from relprim.pydantic import pydantic_validator


result = await (
    async_operation("fetch_user", fetch_user)
    .with_validation(validation_policy(pydantic_validator(ProviderResponse)))
    .run(42)
)
```

## Validation, not conversion

Values are validated with `ProviderResponse.model_validate(value)`.

The validated model instance is discarded. The operation result is returned
unchanged:

```python
result = await fetch_user(42)

result.value  # still the original dict, not a ProviderResponse
```

Pydantic's normal coercion rules apply during validation. For example,
`{"id": "1", "name": "Ada"}` is accepted by the model above, and the result
still contains the string `"1"`.

If you need the parsed model, call `model_validate(...)` on the accepted value
in your own code.

## Strict mode

Pass `strict=True` to disable coercion during validation:

```python
pydantic_validator(ProviderResponse, strict=True)
```

`strict` is forwarded to `model_validate(...)`. The default, `None`, keeps the
model's own configuration.

## Validation failures

When a value does not match the model, the validator returns a failed
`ValidationResult`. RelPrim then raises `ValidationFailedError` for that
attempt through its normal validation machinery.

The reason contains the first Pydantic error's message, location and error
type:

```text
Field required (path: $.name, type: missing)
Field required (path: $.user.email, type: missing)
Input should be a valid integer (path: $.id, type: int_type)
```

Paths start at `$` (the whole value). Fields are written as `.name` and list
indexes as `[1]`.

When there are more errors, their count is appended:

```text
Input should be a valid integer, unable to parse string as an integer (path: $.id, type: int_parsing) (+1 more validation error)
```

Errors are reported in Pydantic's order, so the reason is deterministic for
the same model and value.

Input values and documentation URLs from Pydantic errors are not included in
the reason.

## Retry and fallback

Pydantic failures are ordinary `ValidationFailedError` failures.

Retry them explicitly:

```python
from relprim import ValidationFailedError, resilient


@resilient(
    retries=2,
    retry_on=(ValidationFailedError,),
    validation=validation_policy(pydantic_validator(ProviderResponse)),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

Fallback results are validated with the same policy. A fallback can recover
from a primary result that failed validation:

```python
@resilient(
    fallback=fetch_user_from_backup,
    validation=validation_policy(pydantic_validator(ProviderResponse)),
)
async def fetch_user(user_id: int) -> dict[str, object]:
    return await provider.fetch_user(user_id)
```

Validator names and failure reasons appear in execution report metadata and
structured validation events.

## Limitations

* Only Pydantic v2 models (`BaseModel` subclasses) are supported.
* Operation results are never replaced with model instances.
* Only the first error is described in detail.
* Validation runs synchronously inside the attempt, like other validators.

## Dependency model

Pydantic is an optional dependency.

`import relprim` never imports it. Only `relprim.pydantic` requires it, so the
core package remains dependency-free.

See [`examples/pydantic_validation.py`](../examples/pydantic_validation.py)
for a runnable example.
