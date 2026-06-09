from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeAlias, TypeVar

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

RetryOn: TypeAlias = tuple[type[Exception], ...]
TimeoutConfig: TypeAlias = TimeoutPolicy | int | float | None
FallbackCallable: TypeAlias = Callable[P, Awaitable[R]]


def _operation_name_for(operation: Callable[P, Awaitable[R]]) -> str:
    name = getattr(operation, "__name__", None)

    if isinstance(name, str) and name.strip():
        return name

    class_name = operation.__class__.__name__

    if class_name.strip():
        return class_name

    raise ValueError("operation name could not be inferred.")


def _retry_policy_from(
    *,
    retry: RetryPolicy | None,
    retries: int | None,
    retry_on: RetryOn,
) -> RetryPolicy | None:
    if retry is not None and retries is not None:
        raise ValueError("retry and retries cannot be used together.")

    if retry is not None:
        return retry

    if retries is None:
        return None

    if retries < 0:
        raise ValueError("retries must be greater than or equal to 0.")

    if retries == 0:
        return None

    return RetryPolicy(
        max_attempts=retries + 1,
        retry_on=retry_on,
    )


def _timeout_policy_from(timeout: TimeoutConfig) -> TimeoutPolicy | None:
    if timeout is None:
        return None

    if isinstance(timeout, TimeoutPolicy):
        return timeout

    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise TypeError("timeout must be a TimeoutPolicy, int, float or None.")

    if timeout <= 0:
        raise ValueError("timeout must be greater than 0.")

    return TimeoutPolicy(seconds=float(timeout))


def _fallback_chain_from(
    *,
    fallback: FallbackCallable[P, R] | None,
    fallbacks: FallbackChain[P, R] | None,
) -> FallbackChain[P, R] | None:
    if fallback is not None and fallbacks is not None:
        raise ValueError("fallback and fallbacks cannot be used together.")

    if fallbacks is not None:
        return fallbacks

    if fallback is None:
        return None

    return FallbackChain.from_operations(
        (_operation_name_for(fallback), fallback),
    )


def resilient(
    *,
    name: str | None = None,
    retries: int | None = None,
    retry_on: RetryOn = (Exception,),
    retry: RetryPolicy | None = None,
    timeout: TimeoutConfig = None,
    fallback: FallbackCallable[P, R] | None = None,
    fallbacks: FallbackChain[P, R] | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    validation: ValidationPolicy[R] | None = None,
    events: EventEmitter | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[OperationResult[R]]]]:
    """Wrap an async callable in a resilient RelPrim operation.

    The decorated function returns OperationResult[T], not a raw value. This keeps
    execution metadata explicit and makes retries, fallbacks, validation and
    structured events observable by default.

    Simple usage:
        @resilient(retries=3, timeout=10, fallback=call_backup_provider)
        async def call_provider(prompt: str) -> str:
            ...

    Advanced usage:
        @resilient(
            name="generate_response",
            retry=RetryPolicy(max_attempts=3),
            timeout=TimeoutPolicy(seconds=10),
            fallbacks=fallback_chain(
                ("gemini_provider", call_gemini),
                ("local_cache", call_cached_response),
            ),
        )
        async def generate_response(prompt: str) -> str:
            ...
    """
    if name is not None and not name.strip():
        raise ValueError("name must not be empty.")

    retry_policy = _retry_policy_from(
        retry=retry,
        retries=retries,
        retry_on=retry_on,
    )
    timeout_policy = _timeout_policy_from(timeout)
    fallback_chain_policy = _fallback_chain_from(
        fallback=fallback,
        fallbacks=fallbacks,
    )

    def decorator(
        operation: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[OperationResult[R]]]:
        operation_name = name if name is not None else _operation_name_for(operation)

        configured_operation = async_operation(operation_name, operation)

        if events is not None:
            configured_operation = configured_operation.with_events(events)

        if circuit_breaker is not None:
            configured_operation = configured_operation.with_circuit_breaker(circuit_breaker)

        if retry_policy is not None:
            configured_operation = configured_operation.with_retry(retry_policy)

        if timeout_policy is not None:
            configured_operation = configured_operation.with_timeout(timeout_policy)

        if validation is not None:
            configured_operation = configured_operation.with_validation(validation)

        if fallback_chain_policy is not None:
            configured_operation = configured_operation.with_fallbacks(fallback_chain_policy)

        @wraps(operation)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> OperationResult[R]:
            return await configured_operation.run(*args, **kwargs)

        return wrapper

    return decorator
