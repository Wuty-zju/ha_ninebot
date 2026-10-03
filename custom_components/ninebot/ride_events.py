"""Conservative, bounded ride-event cursor, independent of HA or transport.

New end-time reports after a live baseline may signal a completed cloud ride.
There is no push guarantee or recovery replay. The upload-delay window is a
local policy, not a measured promise by Ninebot. Persistence precedes emission.
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .ride_models import Ride
from .travel import opaque_id

SEEN_LIMIT = 128
LATE_WINDOW = timedelta(minutes=30)
REBASELINE_GAP = timedelta(hours=24)


class InvalidRideCursor(ValueError):
    """Do not silently overwrite an unsupported or corrupt cursor."""


def ride_key(ride_id: str) -> str:
    return hashlib.sha256(ride_id.encode()).hexdigest()


def completed_report(ride: Ride, now: datetime) -> bool:
    """Require identity, reliable past start/end and a consistent positive duration."""
    return bool(
        ride.ride_id
        and opaque_id(ride.ride_id) == ride.ride_id
        and ride.started_at
        and ride.ended_at
        and ride.started_at < ride.ended_at <= now
        and ride.duration_s is not None
        and ride.duration_s > 0
        and abs((ride.ended_at - ride.started_at).total_seconds() - ride.duration_s) <= 1
        and not {
            "conflicting_time_representations",
            "conflicting_ride_ids",
            "conflicting_detail_ids",
            "reversed_timestamps",
            "duration_time_difference",
            "invalid_timestamp",
        }.intersection(ride.issues)
    )


@dataclass(frozen=True)
class RideCursor:
    baseline_at: datetime | None = None
    observed_at: datetime | None = None
    high_water_end: datetime | None = None
    retention_floor: datetime | None = None
    seen: tuple[tuple[str, datetime], ...] = ()

    def dump(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "baseline_at": iso(self.baseline_at),
            "observed_at": iso(self.observed_at),
            "high_water_end": iso(self.high_water_end),
            "retention_floor": iso(self.retention_floor),
            "seen": [[key, iso(ended_at)] for key, ended_at in self.seen],
        }

    @classmethod
    def restore(cls, raw: object) -> "RideCursor":
        if not isinstance(raw, dict) or not {
            "baseline_at",
            "observed_at",
            "high_water_end",
            "retention_floor",
            "seen",
        }.issubset(raw):
            raise InvalidRideCursor

        def date(value: object) -> datetime | None:
            if value is None:
                return None
            if not isinstance(value, str) or len(value) > 40:
                raise InvalidRideCursor
            try:
                result = datetime.fromisoformat(value)
            except ValueError as err:
                raise InvalidRideCursor from err
            if result.tzinfo is None or not 2000 <= result.year <= 2099:
                raise InvalidRideCursor
            return result.astimezone(UTC)

        fields = [
            date(raw.get(key))
            for key in ("baseline_at", "observed_at", "high_water_end", "retention_floor")
        ]
        rows = raw.get("seen")
        if not isinstance(rows, list) or len(rows) > SEEN_LIMIT:
            raise InvalidRideCursor
        seen = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise InvalidRideCursor
            key, ended_at = row[0], date(row[1])
            if (
                not isinstance(key, str)
                or len(key) != 64
                or any(char not in "0123456789abcdef" for char in key)
                or ended_at is None
            ):
                raise InvalidRideCursor
            seen.append((key, ended_at))
        if len({key for key, _ in seen}) != len(seen):
            raise InvalidRideCursor
        baseline, observed, high, floor = fields
        if (
            (baseline is None and (seen or high or floor))
            or (baseline and (observed is None or baseline > observed))
            or (high and (observed is None or high > observed))
            or (floor and (high is None or floor > high))
            or any(observed is None or ended > observed for _, ended in seen)
        ):
            raise InvalidRideCursor
        return cls(baseline, observed, high, floor, tuple(seen))


@dataclass(frozen=True)
class RideDiscovery:
    cursor: RideCursor
    rides: tuple[Ride, ...] = ()
    reason: str = "unchanged"


def _retain(cursor: RideCursor, observed: dict[str, datetime]) -> RideCursor:
    ordered = sorted(
        (
            row
            for row in observed.items()
            if cursor.retention_floor is None or row[1] > cursor.retention_floor
        ),
        key=lambda row: (row[1], row[0]),
    )
    if len(ordered) > SEEN_LIMIT:
        # IDs evicted inside the overlap window cannot safely be re-emitted.
        # Move a conservative timestamp floor past all evicted IDs, including
        # timestamp ties. This may suppress a later arrival; it never replays it.
        evicted_end = ordered[-SEEN_LIMIT - 1][1]
        floor = max(cursor.retention_floor or evicted_end, evicted_end)
        cursor = replace(cursor, retention_floor=floor)
        ordered = [row for row in ordered[-SEEN_LIMIT:] if row[1] > floor]
    return replace(cursor, seen=tuple(ordered))


def discover_rides(
    cursor: RideCursor, rides: tuple[Ride, ...], now: datetime, *, baseline: bool = False
) -> RideDiscovery:
    """Compare sets, not server ordering or numeric IDs. No catch-up flood."""
    valid: dict[str, Ride] = {}
    conflicting: set[str] = set()
    for ride in rides:
        if not completed_report(ride, now):
            continue
        assert ride.ride_id is not None
        key = ride_key(ride.ride_id)
        previous = valid.get(key)
        if previous and (
            previous.started_at != ride.started_at or previous.ended_at != ride.ended_at
        ):
            conflicting.add(key)
        valid[key] = ride
    for key in conflicting:
        valid.pop(key)
    if (
        baseline
        or cursor.baseline_at is None
        or (
            cursor.observed_at is None
            or now < cursor.observed_at
            or now - cursor.observed_at > REBASELINE_GAP
        )
    ):
        if not valid:
            return RideDiscovery(RideCursor(), reason="awaiting_baseline")
        known = {key: ride.ended_at for key, ride in valid.items() if ride.ended_at is not None}
        initial = _retain(
            RideCursor(now, now, max(known.values())),
            known,
        )
        return RideDiscovery(initial, reason="baseline")
    seen = dict(cursor.seen)
    found = []
    for key, ride in valid.items():
        assert ride.ended_at is not None
        if key in seen:
            continue
        if (
            ride.ended_at > cursor.baseline_at
            and ride.ended_at >= now - LATE_WINDOW
            and (cursor.retention_floor is None or ride.ended_at > cursor.retention_floor)
        ):
            found.append(ride)
        seen[key] = ride.ended_at
    high = max(
        (
            end
            for end in (cursor.high_water_end, *(ride.ended_at for ride in valid.values()))
            if end
        ),
        default=None,
    )
    updated = _retain(replace(cursor, observed_at=now, high_water_end=high), seen)
    found.sort(key=lambda ride: (ride.ended_at, ride.ride_id or ""))
    # If a single new batch exceeds capacity, suppress evicted candidates before
    # persistence; otherwise the bounded ledger could not prove duplicate safety.
    retained = {key for key, _ in updated.seen}
    return RideDiscovery(
        updated,
        tuple(ride for ride in found if ride_key(ride.ride_id or "") in retained),
        "discovered" if found else "observed",
    )
