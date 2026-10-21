"""Polling targets, observation validity and query caches are separate policies."""

from collections.abc import Mapping
from dataclasses import dataclass

MONTH_CACHE_SECONDS = 600
TRIP_DETAIL_CACHE_SECONDS = 900
RIDE_COMPLETION_QUIET_SECONDS = 600
DAY_OBSERVATION_MAX_AGE = 1800
DAILY_REPAIR_INTERVAL = 60


@dataclass(frozen=True)
class PollingPolicy:
    status: int = 60
    battery: int = 60
    travel: int = 600
    profile: int = 3600
    name: str = "balanced"

    @classmethod
    def from_options(cls, options: Mapping[str, object]) -> "PollingPolicy":
        """Retain custom status intervals until the owner selects a preset."""
        value = options.get("poll_interval", 60)
        try:
            interval = (
                max(30, min(3600, int(value)))
                if isinstance(value, (int, float, str)) and not isinstance(value, bool)
                else 60
            )
        except (TypeError, ValueError, OverflowError):
            interval = 60
        if interval == 60:
            return cls()
        return cls(status=interval, battery=600, name="legacy")

    def interval(self, group: str) -> int:
        return {
            "status": self.status,
            "battery": self.battery,
            "travel": self.travel,
            "profile": self.profile,
        }[group]

    def max_age(self, group: str) -> int:
        """A temporary fast target never shortens observation validity."""
        return {
            "status": max(180, 3 * self.status),
            "battery": max(1800, 3 * self.battery),
            "travel": max(1800, 3 * self.travel),
            "profile": 3 * self.profile,
        }[group]
