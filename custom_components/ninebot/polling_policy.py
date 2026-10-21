"""Polling targets, observation validity and query caches are separate policies."""

from collections.abc import Mapping
from dataclasses import dataclass

CONF_POLLING_PROFILE = "polling_profile"
POLLING_OPTIONS = frozenset({CONF_POLLING_PROFILE, "poll_interval"})
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
    charging: tuple[int, int] = (15, 30)
    active: tuple[int, int, int] = (10, 30, 120)
    short: tuple[int, int] = (5, 15)

    @classmethod
    def from_options(cls, options: Mapping[str, object]) -> "PollingPolicy":
        """Use Balanced unless a supported profile has already been selected."""
        preset = options.get(CONF_POLLING_PROFILE)
        if isinstance(preset, str) and preset in PRESETS:
            return PRESETS[preset]
        return PRESETS["balanced"]

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

    def scene_interval(self, group: str, reason: str) -> int:
        targets = {
            "charging": dict(zip(("status", "battery"), self.charging, strict=True)),
            "active": dict(zip(("status", "battery", "travel"), self.active, strict=True)),
            "charge_transition": {
                "status": max(self.short[0], min(self.charging[0], self.active[0])),
                "battery": self.short[1],
            },
            "recent_unlock": {"status": self.short[0]},
            "recent_stop": {
                "status": max(self.short[0], self.active[0]),
                "battery": self.active[1],
            },
        }
        return min(self.interval(group), targets.get(reason, {}).get(group, self.interval(group)))


PRESETS: dict[str, PollingPolicy] = {
    "very_fast": PollingPolicy(
        15, 30, 300, name="very_fast", charging=(5, 10), active=(2, 10, 30), short=(2, 5)
    ),
    "faster": PollingPolicy(
        30, 30, 600, name="faster", charging=(10, 15), active=(5, 15, 60), short=(5, 10)
    ),
    "balanced": PollingPolicy(),
    "slower": PollingPolicy(
        120, 180, 1200, name="slower", charging=(30, 60), active=(30, 60, 300), short=(15, 30)
    ),
    "very_slow": PollingPolicy(
        300, 600, 1800, name="very_slow", charging=(60, 120), active=(60, 180, 600), short=(30, 60)
    ),
}
