"""Opt-in cloud coordinates: the upstream coordinate reference is unverified."""

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_COORDINATES
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotTracker(NinebotEntity, TrackerEntity):
    _attr_source_type = SourceType.GPS
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "location", "device_tracker", "status")

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.entry.options.get(CONF_COORDINATES))
            and self.latitude is not None
            and self.longitude is not None
        )

    @property
    def latitude(self) -> float | None:
        return (
            self.snapshot.status.latitude
            if self.snapshot and self.entry.options.get(CONF_COORDINATES)
            else None
        )

    @property
    def longitude(self) -> float | None:
        return (
            self.snapshot.status.longitude
            if self.snapshot and self.entry.options.get(CONF_COORDINATES)
            else None
        )

    @property
    def entity_picture(self) -> str | None:
        return self.snapshot.profile.image_url if self.snapshot else None


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotTracker(entry, sn)])
