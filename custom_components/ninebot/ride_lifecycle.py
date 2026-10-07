"""Bounded fresh-sample stability, a local policy rather than a cloud final flag."""

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .const import DETAIL_INTERVAL
from .ride_events import SEEN_LIMIT, completed_report, ride_key
from .ride_models import Ride

QUIET_WINDOW = timedelta(seconds=DETAIL_INTERVAL)


def fingerprint(ride: Ride) -> str:
    """Mutable physical metrics must settle; identity is independent of them."""
    values = (
        ride.started_at.isoformat() if ride.started_at else None,
        ride.ended_at.isoformat() if ride.ended_at else None,
        ride.distance_m,
        ride.duration_s,
        ride.energy_raw,
        ride.server_max_speed_m_s,
    )
    return hashlib.sha256(json.dumps(values, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class RideObservation:
    signature: str
    changed_at: datetime
    received_at: datetime
    samples: int = 1
    revised: bool = False

    @property
    def stable(self) -> bool:
        return self.samples >= 2 and self.received_at - self.changed_at >= QUIET_WINDOW


class RideLifecycle:
    """No timers, I/O, raw payload or restored fresh observations."""

    def __init__(self) -> None:
        self._observations: OrderedDict[str, RideObservation] = OrderedDict()

    def observe(self, rides: tuple[Ride, ...], received_at: datetime) -> None:
        valid: dict[str, str] = {}
        conflicting: set[str] = set()
        for ride in rides:
            if not completed_report(ride, received_at):
                if ride.ride_id:
                    conflicting.add(ride_key(ride.ride_id))
                continue
            assert ride.ride_id is not None
            key = ride_key(ride.ride_id)
            try:
                signature = fingerprint(ride)
            except (ValueError, TypeError):
                conflicting.add(key)
                continue
            if key in valid and valid[key] != signature:
                conflicting.add(key)
            valid[key] = signature
        for key in conflicting:
            valid.pop(key, None)
            self._observations.pop(key, None)
        for key, signature in valid.items():
            previous = self._observations.get(key)
            if previous and received_at <= previous.received_at:
                # Reading an unchanged cache or an older fallback isn't a sample.
                if received_at == previous.received_at and signature != previous.signature:
                    self._observations.pop(key, None)
                continue
            if previous and signature == previous.signature:
                observation = RideObservation(
                    signature,
                    previous.changed_at,
                    received_at,
                    min(2, previous.samples + 1),
                    previous.revised,
                )
            else:
                observation = RideObservation(
                    signature,
                    received_at,
                    received_at,
                    revised=bool(previous and (previous.stable or previous.revised)),
                )
            self._observations[key] = observation
            self._observations.move_to_end(key)
            while len(self._observations) > SEEN_LIMIT:
                self._observations.popitem(last=False)

    def stable_rides(self, rides: tuple[Ride, ...]) -> tuple[Ride, ...]:
        return tuple(ride for ride in rides if self.phase(ride) == "finalized_by_policy")

    def phase(self, ride: Ride) -> str:
        observation = self._observations.get(ride_key(ride.ride_id)) if ride.ride_id else None
        if observation is None:
            return "reported"
        try:
            if observation.signature != fingerprint(ride):
                return "reported"
        except (ValueError, TypeError):
            return "reported"
        if observation.stable:
            return "finalized_by_policy"
        return "revised" if observation.revised else "stabilizing"
