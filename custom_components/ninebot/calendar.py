"""One read-only local ride calendar per vehicle; never fetch history on browse."""

import hashlib
from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .archive_runtime import ArchiveStatistics
from .archive_timeline import SELECTION_BASIS
from .compat import update_calendar_listeners
from .const import DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .ride_archive import ArchiveError, ArchiveFailure
from .ride_models import Ride
from .runtime import NinebotConfigEntry


class NinebotRideCalendar(NinebotEntity, CalendarEntity):
    """Completed observations, not scheduled rides or a complete cloud history."""

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "ride_calendar", "calendar", "travel")
        self._labels: dict[str, str] = {}
        self._last_available: bool | None = None

    @property
    def event(self) -> CalendarEvent | None:
        # Past rides do not become the next event or an active ride indication.
        return None

    @property
    def available(self) -> bool:
        statistics = self.coordinator.statistics
        return (
            self.coordinator.local_vehicle_available(self.sn)
            and isinstance(statistics, ArchiveStatistics)
            and statistics.archive_ready
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "source": "ride_archive",
            "coverage": "observed_subset",
            "selection_basis": SELECTION_BASIS,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._last_available = self.available
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "entity", {DOMAIN}
        )
        self._labels = {
            key: translations.get(f"component.{DOMAIN}.entity.sensor.{key}.name", key)
            for key in (
                "last_mileage",
                "last_ride_duration",
                "last_ride_max_speed",
                "last_ride_average_speed",
                "last_energy_raw",
            )
        }
        if isinstance(statistics := self.coordinator.statistics, ArchiveStatistics):
            self.async_on_remove(
                statistics.async_add_archive_listener(self.sn, self._archive_updated)
            )

    @callback
    def _archive_updated(self) -> None:
        # The state remains off. Notify ranges directly, without duplicate
        # subscription reads through the Core state-write debouncer.
        update_calendar_listeners(self)

    @callback
    def _handle_coordinator_update(self) -> None:
        # Status/BMS telemetry cannot change historical calendar contents.
        # Newer Core otherwise expands subscribed ranges on every state write.
        if (available := self.available) != self._last_available:
            self._last_available = available
            self.async_write_ha_state()

    def _description(self, ride: Ride) -> str:
        precision = dict(ride.precision)
        average = ride.average_speed_m_s
        metrics = (
            (
                "last_mileage",
                ride.distance_m / 1000 if ride.distance_m is not None else None,
                "km",
                precision.get("mileages", 2),
            ),
            ("last_ride_duration", ride.duration_s, "s", precision.get("duration", 0)),
            (
                "last_ride_max_speed",
                ride.server_max_speed_m_s * 3.6 if ride.server_max_speed_m_s is not None else None,
                "km/h",
                precision.get("speed", 1),
            ),
            (
                "last_ride_average_speed",
                average * 3.6
                if average is not None and "duration_time_difference" not in ride.issues
                else None,
                "km/h",
                1,
            ),
            ("last_energy_raw", ride.energy_raw, "Wh", precision.get("ec", 0)),
        )
        return "\n".join(
            f"{self._labels.get(key, key)}: {value:.{min(max(digits, 0), 6)}f} {unit}"
            for key, value, unit, digits in metrics
            if value is not None
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        statistics = self.coordinator.statistics
        if not self.available or not isinstance(statistics, ArchiveStatistics):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="calendar_unavailable"
            )
        coordinator = self.coordinator
        generation, ownership = coordinator._generation, coordinator._ownership.get(self.sn, 0)
        try:
            view = await statistics.archive.async_timeline(self.sn, start_date, end_date)
        except ValueError:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="calendar_range"
            ) from None
        except ArchiveError as err:
            statistics.record_error(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="calendar_range"
                if err.kind is ArchiveFailure.BUDGET
                else "calendar_unavailable",
            ) from None
        if (
            not self.available
            or generation != coordinator._generation
            or ownership != coordinator._ownership.get(self.sn, 0)
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="calendar_unavailable"
            )
        now = dt_util.utcnow()
        events = []
        name = self.name
        for record in view.records:
            ride = record.ride
            if ride.started_at is None or ride.ended_at is None or ride.ended_at > now:
                continue
            identity = hashlib.sha256(
                f"{self.entry.entry_id}\0{self.sn}\0{ride.ride_id}".encode()
            ).hexdigest()
            events.append(
                CalendarEvent(
                    start=ride.started_at,
                    end=ride.ended_at,
                    summary=name if isinstance(name, str) else "Rides",
                    description=self._description(ride),
                    uid=identity,
                )
            )
        return events


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotRideCalendar(entry, sn)])
