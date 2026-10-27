"""Private configuration checks shared by RelPrim primitives.

Not part of the public API.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None


def check_seconds(value: object, name: str, *, allow_zero: bool) -> None:
    """Validate a duration expressed in seconds."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be an int or float.")

    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite.")

    if allow_zero and value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0.")

    if not allow_zero and value <= 0:
        raise ValueError(f"{name} must be greater than 0.")


def check_aware(timestamp: datetime, name: str) -> None:
    """Validate that a datetime is timezone-aware.

    A datetime whose tzinfo returns None from utcoffset() is naive, as defined by
    the datetime module.
    """
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def check_exception_types(value: object, name: str) -> None:
    """Validate a non-empty tuple of Exception subclasses.

    BaseException subclasses such as asyncio.CancelledError and KeyboardInterrupt
    are rejected so cancellation and interpreter exits can never be configured as
    retryable, fallback-worthy or breaker-recorded failures.
    """
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple of Exception subclasses.")

    if not value:
        raise ValueError(f"{name} must contain at least one exception type.")

    for exception_type in value:
        if not isinstance(exception_type, type) or not issubclass(exception_type, Exception):
            raise TypeError(f"{name} must contain Exception subclasses.")


def freeze_json_scalars(
    values: Mapping[str, JsonScalar] | None,
    name: str,
) -> Mapping[str, JsonScalar]:
    """Copy a mapping of JSON scalar values into a read-only mapping."""
    copied = dict(values or {})

    for key, value in copied.items():
        if not isinstance(key, str):
            raise TypeError(f"{name} keys must be strings.")

        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} values must be finite.")

        if value is not None and not isinstance(value, str | int | float):
            raise TypeError(f"{name} values must be str, int, float, bool or None.")

    return MappingProxyType(copied)
