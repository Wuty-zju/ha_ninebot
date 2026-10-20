"""Small display projections; timestamp fields remain available independently."""

from datetime import datetime


def ride_time_range(start: datetime | None, end: datetime | None) -> str | None:
    """ISO endpoints with explicit offsets, compact only inside the same local day."""
    if any(value is not None and value.tzinfo is None for value in (start, end)):
        return None
    if start is None and end is None:
        return None
    if start is not None and end is not None and end < start:
        return None
    finish = end.isoformat() if end else "—"
    if start and end and start.date() == end.date() and start.utcoffset() == end.utcoffset():
        finish = end.timetz().isoformat()
    return f"{start.isoformat() if start else '—'} – {finish}"
