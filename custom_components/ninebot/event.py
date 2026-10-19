"""Optional semantic trip/status events with bounded, GPS-free projections."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.event import EventEntity, EventExtraStoredData
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .archive_runtime import ArchiveStatistics
from .const import CONF_EVENTS, DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .ride_models import Ride
from .ride_presentation import ride_time_range
from .ride_reports import RideReportCursor
from .runtime import NinebotConfigEntry
from .vehicle_events import TRANSITIONS, StatusEventCursor


@dataclass
class RideEventExtraData(EventExtraStoredData):
    """Retain the actual event timestamp through an unavailable HA state."""

    last_triggered_at: str | None = None


class NinebotObservedEvent(NinebotEntity, EventEntity):
    """Keep the last display through unavailable without re-emitting it."""

    @property
    def extra_restore_state_data(self) -> RideEventExtraData:
        data = super().extra_restore_state_data
        return RideEventExtraData(data.last_event_type, data.last_event_attributes, self.state)

    async def async_get_last_state(self) -> State | None:
        """Restore display via HA's normal EventEntity restore, without emission."""
        state = await super().async_get_last_state()
        if state is None or state.state not in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return state
        extra = await self.async_get_last_extra_data()
        data = extra.as_dict() if extra else {}
        value = data.get("last_triggered_at")
        stamp = dt_util.parse_datetime(value) if isinstance(value, str) else None
        if (
            stamp is not None
            and stamp.tzinfo is not None
            and 2000 <= stamp.year <= 2099
            and stamp <= dt_util.utcnow()
            and data.get("last_event_type") in self.event_types
        ):
            return State(self.entity_id, stamp.isoformat(), state.attributes)
        return state


class NinebotRideEvent(NinebotObservedEvent):
    _attr_event_types = ["reported", "updated", "completed", "late_reported"]

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "ride", "event", "travel")
        self._ready = False
        self._reports = RideReportCursor()

    @property
    def available(self) -> bool:
        return bool(
            self.coordinator.local_vehicle_available(self.sn)
            and self.entry.runtime_data.events
            and self.entry.runtime_data.events.available
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain an empty event without synthesizing a historical trigger."""
        attributes: dict[str, Any] = {
            "observation_status": "event_recorded" if self.state else "waiting_for_new_ride",
            "travel_fresh": self.coordinator.fresh(self.sn, "travel"),
            "historical_replay": False,
        }
        statistics = self.coordinator.statistics
        latest = (
            statistics.latest_ride(self.sn) if isinstance(statistics, ArchiveStatistics) else None
        )
        ride = latest.ride if latest else None
        snapshot = self.snapshot
        if snapshot and snapshot.travel and snapshot.travel.last_ride:
            reported = snapshot.travel.last_ride.ride
            if (
                reported
                and reported.ended_at
                and (not ride or not ride.ended_at or reported.ended_at > ride.ended_at)
            ):
                ride = reported
        if ride:
            attributes["latest_ride"] = self._ride_data(ride)
            attributes["latest_start_time"] = (
                ride.started_at.isoformat() if ride.started_at else None
            )
            attributes["latest_end_time"] = ride.ended_at.isoformat() if ride.ended_at else None
        return attributes

    @staticmethod
    def _ride_data(ride: Ride) -> dict[str, Any]:
        return {
            "ride_id": ride.ride_id,
            "query_month": ride.query_month,
            "start_time": ride.started_at.isoformat() if ride.started_at else None,
            "end_time": ride.ended_at.isoformat() if ride.ended_at else None,
            "time_range": ride_time_range(ride.started_at, ride.ended_at),
            "distance_m": ride.distance_m,
            "duration_s": ride.duration_s,
            "max_speed_m_s": ride.server_max_speed_m_s,
            "average_speed_m_s": ride.average_speed_m_s,
            "energy_wh": ride.energy_raw,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._observe_reports()  # Rebaseline; do not replay a restored/imported ride.
        if pipeline := self.entry.runtime_data.events:
            self.async_on_remove(await pipeline.async_subscribe(self.sn, self._completed))
        if isinstance(statistics := self.coordinator.statistics, ArchiveStatistics):
            self.async_on_remove(
                statistics.async_add_archive_listener(self.sn, self._archive_updated)
            )
        self._ready = True

    @callback
    def _archive_updated(self) -> None:
        self.async_write_ha_state()  # Update latest timestamps without firing a historical event.

    def _observe_reports(self) -> None:
        snapshot = self.snapshot
        if (
            snapshot is None
            or snapshot.travel is None
            or not self.coordinator.fresh(self.sn, "travel")
            or snapshot.travel_freshness.error is not None
            or snapshot.travel_freshness.succeeded_at is None
        ):
            self._reports = RideReportCursor()
            return
        records = snapshot.travel.rides
        for kind, ride in self._reports.observe(
            records,
            snapshot.travel_freshness.succeeded_at,
            (self.coordinator._generation, self.coordinator._ownership.get(self.sn, 0)),
        ):
            self._trigger_event(kind, {**self._ride_data(ride), "source": "cloud_travel_report"})
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._ready:
            self._observe_reports()
            self.async_write_ha_state()

    @callback
    def _completed(self, ride: Ride, late: bool) -> None:
        self._trigger_event(
            "late_reported" if late else "completed",
            {
                **self._ride_data(ride),
                "source": "cloud_travel_end_report",
                "completion_basis": "stable_successful_samples",
                "late": late,
            },
        )
        self.async_write_ha_state()


class NinebotVehicleEvent(NinebotObservedEvent):
    """One batch per successful status receipt, including on older Core."""

    # The legacy type remains restorable, but new receipts emit concrete types.
    _attr_event_types = ["state_changed", *(kind for pair in TRANSITIONS for kind in pair)]

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "vehicle_event", "event", "status")
        self._ready = False
        self._cursor = StatusEventCursor()
        self._change_labels: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return self.coordinator.local_vehicle_available(self.sn)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "observation_status": "event_recorded" if self.state else "waiting_for_transition",
            "status_fresh": self.coordinator.status_observation(self.sn) is not None,
            "historical_replay": False,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "entity", {DOMAIN}
        )
        prefix = f"component.{DOMAIN}.entity.event.vehicle_event.state_attributes.changes.state."
        self._change_labels = {
            key.removeprefix(prefix): value
            for key, value in translations.items()
            if key.startswith(prefix)
        }
        self._observe()  # Baseline only, including when a prior display was restored.
        self._ready = True

    def _observe(self) -> None:
        sample = self.coordinator.status_observation(self.sn)
        changes = self._cursor.observe(sample, max_gap_s=max(3 * self.coordinator.interval, 180))
        if sample is not None and changes:
            for change in changes:
                self._trigger_event(
                    change,
                    {
                        "changes": list(changes),
                        "summary": self._change_labels.get(change, change),
                        "observed_at": sample.received_at.isoformat(),
                        "observation_revision": sample.identity[2],
                        "source": "cloud_status_observation",
                        "completion_basis": "successful_status_transition",
                    },
                )
                self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self._ready:
            return
        self._observe()
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    if not entry.options.get(CONF_EVENTS, True):
        return
    async_setup_dynamic(
        hass, entry, add, lambda sn: [NinebotRideEvent(entry, sn), NinebotVehicleEvent(entry, sn)]
    )
