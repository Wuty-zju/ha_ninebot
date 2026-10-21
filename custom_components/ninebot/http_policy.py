"""Shared bounded HTTP rate-limit policy for both read transports."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def retry_after_seconds(value: str | None, now: datetime) -> float | None:
    """Only a bounded transport header; no guessing from upstream error prose."""
    if value is None or len(value) > 128:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            seconds = (stamp - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(900, seconds)


def rate_limit_delay(value: str | None, now: datetime) -> float:
    delay = retry_after_seconds(value, now)
    return delay if delay is not None else 60
