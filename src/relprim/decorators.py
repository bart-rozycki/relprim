from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeAlias, TypeVar

from relprim.circuit_breaker import CircuitBreaker
from relprim.events import EventEmitter
from relprim.fallback import FallbackChain
from relprim.idempotency import (
    IdempotencyPolicy,
    IdempotencyStore,
    idempotency_policy,
)
from relprim.operation import async_operation
from relprim.rate_limit import (
    RateLimitPolicy,
    RetryAfterExtractor,
    rate_limit_policy,
)
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


def _idempotency_policy_from(
    *,
    idempotency: IdempotencyPolicy[P] | None,
    idempotency_key: Callable[P, str] | None,
    idempotency_store: IdempotencyStore | None,
    idempotency_ttl: float | None,
) -> IdempotencyPolicy[P] | None:
    simple_configuration_used = (
        idempotency_key is not None or idempotency_store is not None or idempotency_ttl is not None
    )

    if idempotency is not None and simple_configuration_used:
        raise ValueError(
            "idempotency cannot be combined with idempotency_key, "
            "idempotency_store or idempotency_ttl."
        )

    if idempotency is not None:
        return idempotency

    if idempotency_key is None:
        if idempotency_store is not None or idempotency_ttl is not None:
            raise ValueError("idempotency_store and idempotency_ttl require idempotency_key.")

        return None

    ttl_seconds = 3600.0 if idempotency_ttl is None else idempotency_ttl

    return idempotency_policy(
        idempotency_key,
        store=idempotency_store,
        ttl_seconds=ttl_seconds,
    )


def _rate_limit_policy_from(
    *,
    rate_limit: RateLimitPolicy | None,
    rate_limit_on: tuple[type[Exception], ...] | None,
    retry_after: RetryAfterExtractor | None,
    max_rate_limit_wait: float | None,
) -> RateLimitPolicy | None:
    simple_configuration_used = (
        rate_limit_on is not None or retry_after is not None or max_rate_limit_wait is not None
    )

    if rate_limit is not None and simple_configuration_used:
        raise ValueError(
            "rate_limit cannot be combined with rate_limit_on, retry_after or max_rate_limit_wait."
        )

    if rate_limit is not None:
        return rate_limit

    if rate_limit_on is None:
        if retry_after is not None or max_rate_limit_wait is not None:
            raise ValueError("retry_after and max_rate_limit_wait require rate_limit_on.")

        return None

    max_wait_seconds = 60.0 if max_rate_limit_wait is None else max_rate_limit_wait

    return rate_limit_policy(
        rate_limit_on=rate_limit_on,
        retry_after=retry_after,
        max_wait_seconds=max_wait_seconds,
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
    idempotency: IdempotencyPolicy[P] | None = None,
    idempotency_key: Callable[P, str] | None = None,
    idempotency_store: IdempotencyStore | None = None,
    idempotency_ttl: float | None = None,
    rate_limit: RateLimitPolicy | None = None,
    rate_limit_on: tuple[type[Exception], ...] | None = None,
    retry_after: RetryAfterExtractor | None = None,
    max_rate_limit_wait: float | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[OperationResult[R]]]]:
    """Wrap an async callable in a resilient RelPrim operation.

    The decorated function returns OperationResult[T], not a raw value. This keeps
    execution metadata explicit and makes retries, timeouts, fallbacks, validation,
    circuit breaker behavior, idempotency, rate-limit recovery and structured
    events observable.

    Simple usage:
        @resilient(
            retries=3,
            timeout=10,
            fallback=call_backup_provider,
            idempotency_key=lambda request_id, payload: request_id,
            rate_limit_on=(ProviderRateLimitError,),
            retry_after=provider_retry_after,
            max_rate_limit_wait=30,
        )
        async def create_payment(
            request_id: str,
            payload: PaymentPayload,
        ) -> Payment:
            ...

    Advanced usage:
        @resilient(
            name="create_payment",
            retry=RetryPolicy(max_attempts=3),
            timeout=TimeoutPolicy(seconds=10),
            fallbacks=fallback_chain(
                ("backup_provider", call_backup_provider),
            ),
            idempotency=idempotency_policy(
                lambda request_id, payload: request_id,
                store=shared_store,
                ttl_seconds=3600,
            ),
            rate_limit=RateLimitPolicy(
                rate_limit_on=(ProviderRateLimitError,),
                retry_after=provider_retry_after,
                max_wait_seconds=30,
            ),
        )
        async def create_payment(
            request_id: str,
            payload: PaymentPayload,
        ) -> Payment:
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
    resolved_idempotency_policy = _idempotency_policy_from(
        idempotency=idempotency,
        idempotency_key=idempotency_key,
        idempotency_store=idempotency_store,
        idempotency_ttl=idempotency_ttl,
    )
    resolved_rate_limit_policy = _rate_limit_policy_from(
        rate_limit=rate_limit,
        rate_limit_on=rate_limit_on,
        retry_after=retry_after,
        max_rate_limit_wait=max_rate_limit_wait,
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

        if resolved_idempotency_policy is not None:
            configured_operation = configured_operation.with_idempotency(
                resolved_idempotency_policy
            )

        if resolved_rate_limit_policy is not None:
            configured_operation = configured_operation.with_rate_limit(resolved_rate_limit_policy)

        @wraps(operation)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> OperationResult[R]:
            return await configured_operation.run(*args, **kwargs)

        return wrapper

    return decorator
