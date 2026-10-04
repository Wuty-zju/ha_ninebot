"""A small, optional cloud-reported ride completion event, without GPS."""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import NinebotEntity, async_setup_dynamic
from .ride_models import Ride
from .runtime import NinebotConfigEntry


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
                "late": late,
            },
        )
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotRideEvent(entry, sn)])
