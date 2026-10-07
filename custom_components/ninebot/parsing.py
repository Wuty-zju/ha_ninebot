"""Shared strict JSON scalar/month validation without domain-model imports."""

import math
from datetime import datetime
from decimal import Decimal
from typing import Any

from .exceptions import ErrorKind, NinebotError

type JsonObject = dict[str, Any]
type JsonScalar = str | int | float | bool | None


def numeric_precision(raw: JsonObject, keys: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """Preserve finite decimal representation scale, not physical accuracy."""
    result = []
    for key in keys:
        value = raw.get(key)
        if number(value) is None:
            continue
        exponent = Decimal(str(value)).as_tuple().exponent
        if isinstance(exponent, int) and -12 <= exponent <= 12:
            result.append((key, max(0, -exponent)))
    return tuple(result)


def raw_scalar(value: object) -> JsonScalar:
    """Keep audited scalar types without allowing large objects into HA state."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value if len(value) <= 255 and not any(ord(c) < 32 for c in value) else None
    if type(value) is int:
        return value if abs(value) <= 10**18 else None
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def display_scalar(value: JsonScalar) -> str | int | float | None:
    """Raw booleans are text, never a guessed binary sensor interpretation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return None if value == "" else value


def integer(value: object, low: int = 0, high: int = 10**9) -> int | None:
    parsed = number(value, low, high)
    return int(parsed) if parsed is not None and parsed.is_integer() else None


def number(value: object, low: float = -math.inf, high: float = math.inf) -> float | None:
    """Reject bool, NaN, Infinity and values outside the physical domain."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and low <= result <= high else None


def boolean(value: object) -> bool | None:
    """Accept only documented binary encodings, never Python truthiness."""
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    if isinstance(value, str) and value in ("0", "1"):
        return value == "1"
    return None


def text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def payload(raw: object) -> JsonObject:
    if not isinstance(raw, dict):
        raise NinebotError(ErrorKind.PROTOCOL)
    return raw


def previous_month(month: str) -> str:
    if len(month) != 6 or not month.isdecimal():
        raise ValueError("Invalid month")
    date = datetime.strptime(month, "%Y%m")
    return f"{date.year - 1}12" if date.month == 1 else f"{date.year}{date.month - 1:02}"
