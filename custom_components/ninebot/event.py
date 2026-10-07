"""A small, optional cloud-reported ride completion event, without GPS."""

from dataclasses import dataclass

from homeassistant.components.event import EventEntity, EventExtraStoredData
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import NinebotEntity, async_setup_dynamic
from .ride_models import Ride
from .runtime import NinebotConfigEntry


@dataclass
class RideEventExtraData(EventExtraStoredData):
    """Retain the actual event timestamp through an unavailable HA state."""

    last_triggered_at: str | None = None


class NinebotRideEvent(NinebotEntity, EventEntity):
    _attr_event_types = ["completed"]

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "ride", "event", "travel")

    @property
    def available(self) -> bool:
        return bool(
            super().available
            and self.entry.runtime_data.events
            and self.entry.runtime_data.events.available
        )

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
            and data.get("last_event_type") == "completed"
        ):
            return State(self.entity_id, stamp.isoformat(), state.attributes)
        return state

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if pipeline := self.entry.runtime_data.events:
            self.async_on_remove(await pipeline.async_subscribe(self.sn, self._completed))

    @callback
    def _completed(self, ride: Ride, late: bool) -> None:
        self._trigger_event(
            "completed",
            {
                "ride_id": ride.ride_id,
                "query_month": ride.query_month,
                "start_time": ride.started_at.isoformat() if ride.started_at else None,
                "end_time": ride.ended_at.isoformat() if ride.ended_at else None,
                "distance_m": ride.distance_m,
                "duration_s": ride.duration_s,
                "max_speed_m_s": ride.server_max_speed_m_s,
                "average_speed_m_s": ride.average_speed_m_s,
                "source": "cloud_travel_end_report",
                "completion_basis": "stable_successful_samples",
                "late": late,
            },
        )
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotRideEvent(entry, sn)])
