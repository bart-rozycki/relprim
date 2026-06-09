from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from relprim.circuit_breaker import CircuitBreaker
from relprim.events import EventEmitter
from relprim.fallback import FallbackChain
from relprim.operation import async_operation
from relprim.result import OperationResult
from relprim.retry import RetryPolicy
from relprim.timeout import TimeoutPolicy
from relprim.validation import ValidationPolicy

P = ParamSpec("P")
R = TypeVar("R")


def _operation_name_for(operation: Callable[P, Awaitable[R]]) -> str:
    name = getattr(operation, "__name__", None)

    if isinstance(name, str) and name.strip():
        return name

    class_name = operation.__class__.__name__

    if class_name.strip():
        return class_name

    raise ValueError("operation name could not be inferred.")


def resilient(
    *,
    name: str | None = None,
    retry: RetryPolicy | None = None,
    timeout: TimeoutPolicy | None = None,
    fallbacks: FallbackChain[P, R] | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    validation: ValidationPolicy[R] | None = None,
    events: EventEmitter | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[OperationResult[R]]]]:
    """Wrap an async callable in a resilient RelPrim operation.

    The decorated function returns OperationResult[T], not a raw value. This keeps
    execution metadata explicit and makes retries, fallbacks, validation and
    structured events observable by default.

    Example:
        @resilient(
            name="generate_response",
            retry=RetryPolicy(max_attempts=3),
            timeout=TimeoutPolicy(seconds=10),
        )
        async def generate_response(prompt: str) -> str:
            ...

        result = await generate_response("Write a short product summary")
        print(result.value)
        print(result.report.to_dict())
    """
    if name is not None and not name.strip():
        raise ValueError("name must not be empty.")

    def decorator(
        operation: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[OperationResult[R]]]:
        operation_name = name if name is not None else _operation_name_for(operation)

        configured_operation = async_operation(operation_name, operation)

        if events is not None:
            configured_operation = configured_operation.with_events(events)

        if circuit_breaker is not None:
            configured_operation = configured_operation.with_circuit_breaker(circuit_breaker)

        if retry is not None:
            configured_operation = configured_operation.with_retry(retry)

        if timeout is not None:
            configured_operation = configured_operation.with_timeout(timeout)

        if validation is not None:
            configured_operation = configured_operation.with_validation(validation)

        if fallbacks is not None:
            configured_operation = configured_operation.with_fallbacks(fallbacks)

        @wraps(operation)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> OperationResult[R]:
            return await configured_operation.run(*args, **kwargs)

        return wrapper

    return decorator
