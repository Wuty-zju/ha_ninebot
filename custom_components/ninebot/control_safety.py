"""Reviewed lock semantics; absent motion/P evidence never permits locking."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SafetySource(StrEnum):
    UNKNOWN = "unknown"
    REVIEWED_TELEMETRY = "reviewed_telemetry"


@dataclass(frozen=True)
class SafetyObservation:
    """No current ninecli field is mapped here; future adapters need evidence.

    A successful status GET is not the vehicle's report timestamp. ACC, power,
    GPS and historical trip speeds must never populate these properties.
    """

    stopped: bool | None = None
    parked: bool | None = None
    reported_at: datetime | None = None
    source: SafetySource = SafetySource.UNKNOWN

    def permits_lock(self, now: datetime) -> bool:
        return (
            self.stopped is True
            and self.parked is True
            and self.source is SafetySource.REVIEWED_TELEMETRY
            and self.reported_at is not None
            and self.reported_at.tzinfo is not None
            and 0 <= (now - self.reported_at).total_seconds() <= 5
        )


# Only these maintainer-confirmed operations have an observable lock target.
# None is reserved for non-lock controls such as bell.
LOCK_TARGETS: dict[str, tuple[str, bool]] = {
    "engine/start": ("vehicle_lock", False),
    "engine/stop": ("vehicle_lock", True),
    "buck": ("seat_lock", False),
}
