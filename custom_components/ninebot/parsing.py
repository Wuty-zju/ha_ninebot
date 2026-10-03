"""Shared strict JSON scalar/month validation without domain-model imports."""

import math
from datetime import datetime
from typing import Any

from .exceptions import ErrorKind, NinebotError

type JsonObject = dict[str, Any]


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
