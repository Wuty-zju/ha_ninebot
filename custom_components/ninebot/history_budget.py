"""One account-wide policy for background month and detail operations."""

from collections.abc import Mapping
from typing import Any, cast

CONF_BUDGET = "history_requests_per_hour"
DEFAULT_BUDGET = 60
MIN_BUDGET = 1
MAX_BUDGET = 120


def valid_budget(value: object) -> bool:
    return type(value) is int and MIN_BUDGET <= value <= MAX_BUDGET


def configured_budget(options: Mapping[str, Any]) -> int:
    """Keep explicit valid preferences; defaults never overwrite stored choices."""
    value = options.get(CONF_BUDGET, DEFAULT_BUDGET)
    return cast(int, value) if valid_budget(value) else DEFAULT_BUDGET
