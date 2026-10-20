"""Bounded live report changes; never mistake power-on or old imports for a ride."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .ride_events import LATE_WINDOW, SEEN_LIMIT, completed_report, ride_key
from .ride_lifecycle import fingerprint
from .ride_models import Ride


@dataclass
class RideReportCursor:
    previous: dict[str, str] = field(default_factory=dict)
    identity: tuple[int, int] | None = None
    received_at: datetime | None = None

    def observe(
        self, rides: tuple[Ride, ...], received_at: datetime, identity: tuple[int, int]
    ) -> tuple[tuple[str, Ride], ...]:
        valid: dict[str, Ride] = {}
        conflicting: set[str] = set()
        for ride in rides:
            if not completed_report(ride, received_at):
                continue
            key = ride_key(ride.ride_id or "")
            previous_ride = valid.get(key)
            if previous_ride and fingerprint(previous_ride) != fingerprint(ride):
                conflicting.add(key)
            valid[key] = ride
        ordered = sorted(
            (ride for key, ride in valid.items() if key not in conflicting),
            key=lambda ride: (ride.ended_at, ride.ride_id or ""),
        )[-SEEN_LIMIT:]
        latest = {ride_key(ride.ride_id or ""): fingerprint(ride) for ride in ordered}
        previous = self.previous
        baseline = (
            self.identity != identity
            or self.received_at is None
            or not timedelta(0) < received_at - self.received_at <= timedelta(hours=1)
        )
        if self.identity == identity and self.received_at == received_at:
            return ()
        self.identity, self.received_at = identity, received_at
        self.previous = dict(
            list(({**previous, **latest} if not baseline else latest).items())[-SEEN_LIMIT:]
        )
        if baseline:
            return ()
        return tuple(
            ("reported" if ride_key(ride.ride_id or "") not in previous else "updated", ride)
            for ride in ordered
            if ride.ended_at is not None
            and ride.ended_at >= received_at - LATE_WINDOW
            and previous.get(ride_key(ride.ride_id or "")) != latest[ride_key(ride.ride_id or "")]
        )
