"""Enabled entity and internal model dependencies, independent of poll timing."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from .const import VEHICLE_INTERVAL
from .models import VehicleSnapshot

type Group = Literal["status", "battery", "travel"]


class Need(StrEnum):
    PROFILE = "profile"
    STATUS = "status"
    BATTERY = "battery"
    MONTH = "month"
    DAY = "day"
    LAST_RIDE = "last_ride"
    RIDE_EVENT = "ride_event"
    CONTROL = "control"


@dataclass(frozen=True)
class ConsumerContext:
    vehicle: str
    need: Need


@dataclass(frozen=True)
class PollingDemand:
    groups: frozenset[Group]
    last_ride: bool
    reasons: tuple[str, ...]
    previous_month: bool = False

    def diagnostics(self) -> dict[str, object]:
        return {
            "groups": sorted(self.groups),
            "last_ride": self.last_ride,
            "reasons": list(self.reasons),
        }


def entity_context(vehicle: str, platform: str, key: str, group: str) -> ConsumerContext:
    """A disabled entity never adds this context to the coordinator."""
    if platform == "calendar":
        need = Need.MONTH  # Dates are local reads; no adjacent-month cloud backfill.
    elif platform == "event" and key == "ride":
        need = Need.RIDE_EVENT
    elif platform == "button" and key != "refresh":
        need = Need.CONTROL
    elif group == "travel":
        if key.startswith("yesterday_") or key.startswith("today_ride_"):
            need = Need.DAY
        else:
            need = (
                Need.MONTH if key.startswith("month_") or key == "today_mileage" else Need.LAST_RIDE
            )
    elif group == "status":
        need = Need.STATUS
    elif group == "battery":
        need = Need.BATTERY
    else:
        need = Need.PROFILE
    return ConsumerContext(vehicle, need)


def polling_demand(
    snapshot: VehicleSnapshot,
    contexts: Iterable[object],
    *,
    now: datetime,
) -> PollingDemand:
    """Discovery/audit listeners without typed contexts do not imply all groups.

    Battery discovery needs one successful response before it can create its
    entities. An empty/anonymous multi-pack inventory is reprobed hourly, not
    at the normal BMS interval, to discover data that could not be represented.
    Known battery entities that users disable do not keep that probe alive.
    """
    needs = {
        context.need
        for context in contexts
        if isinstance(context, ConsumerContext) and context.vehicle == snapshot.profile.sn
    }
    groups: set[Group] = set()
    reasons = {f"entity_{need.value}" for need in needs}
    if needs & {Need.STATUS, Need.CONTROL}:
        groups.add("status")
    if Need.BATTERY in needs:
        groups.add("battery")
    last_ride = bool(needs & {Need.LAST_RIDE, Need.RIDE_EVENT})
    if last_ride or needs & {Need.MONTH, Need.DAY}:
        groups.add("travel")
    if snapshot.status_freshness.attempted_at is None:
        groups.add("status")
        reasons.add("status_bootstrap")
    if snapshot.travel_freshness.attempted_at is None:
        groups.add("travel")
        last_ride = True  # Legacy enabled entities have not subscribed yet.
        reasons.add("travel_bootstrap")
    battery = snapshot.battery
    success = snapshot.battery_freshness.succeeded_at
    discovery_at = snapshot.battery_freshness.attempted_at or success
    undiscovered = not battery.batteries or (
        len(battery.batteries) > 1 and not any(pack.identified for pack in battery.batteries)
    )
    if success is None or (
        undiscovered
        and discovery_at is not None
        and (now - discovery_at).total_seconds() >= VEHICLE_INTERVAL
    ):
        groups.add("battery")
        reasons.add("battery_discovery")
    if not snapshot.present:
        return PollingDemand(frozenset(), False, ("vehicle_absent",))
    return PollingDemand(frozenset(groups), last_ride, tuple(sorted(reasons)), Need.DAY in needs)
