"""Immutable domain snapshots independent of HA entity metadata."""

from dataclasses import dataclass, field
from datetime import datetime

from .capabilities import VehicleCapabilities
from .exceptions import ErrorKind
from .ride_models import Ride


@dataclass(frozen=True)
class VehicleProfile:
    sn: str
    name: str
    model: str
    image_url: str | None = None


@dataclass(frozen=True)
class VehicleStatus:
    battery: float | None = None
    locked: bool | None = None
    powered: bool | None = None
    charging: bool | None = None
    range_precise: float | None = None
    range_estimated: float | None = None
    range_ai: float | None = None
    charge_remaining: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    capabilities: VehicleCapabilities = field(default_factory=VehicleCapabilities)


@dataclass(frozen=True)
class Battery:
    key: str
    identified: bool
    voltage: float | None
    temperature: float | None
    cycles: int | None
    cycle_supported: bool | None


@dataclass(frozen=True)
class BatteryInfo:
    batteries: tuple[Battery, ...] = ()
    charging_power_raw: float | None = None


@dataclass(frozen=True)
class LastRide:
    month: str
    mileage: float | None
    energy_raw: float | None
    ride_id: str | None
    ride: Ride | None = None


@dataclass(frozen=True)
class TravelMonth:
    month: str
    mileage: float | None = None
    energy_raw: float | None = None
    last_ride: LastRide | None = None
    rides: tuple[Ride, ...] = ()


@dataclass(frozen=True)
class Freshness:
    attempted_at: datetime | None = None
    succeeded_at: datetime | None = None
    error: ErrorKind | None = None

    def valid(self, now: datetime, ttl: float) -> bool:
        """A successful query does not imply a known vehicle report time."""
        return (
            self.succeeded_at is not None and 0 <= (now - self.succeeded_at).total_seconds() <= ttl
        )


@dataclass(frozen=True)
class VehicleSnapshot:
    profile: VehicleProfile
    status: VehicleStatus = field(default_factory=VehicleStatus)
    battery: BatteryInfo = field(default_factory=BatteryInfo)
    travel: TravelMonth | None = None
    status_freshness: Freshness = field(default_factory=Freshness)
    battery_freshness: Freshness = field(default_factory=Freshness)
    travel_freshness: Freshness = field(default_factory=Freshness)
    present: bool = True
    profile_freshness: Freshness = field(default_factory=Freshness)
