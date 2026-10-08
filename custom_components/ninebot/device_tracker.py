"""Opt-in cloud coordinates: the upstream coordinate reference is unverified."""

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_COORDINATES, DEFAULT_COORDINATES
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotTracker(NinebotEntity, TrackerEntity):
    _attr_source_type = SourceType.GPS

    @property
    def entity_category(self) -> EntityCategory | None:
        """Override the tracker's diagnostic default for its primary function."""
        return None

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "location", "device_tracker", "status")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.entry.options.get(CONF_COORDINATES, DEFAULT_COORDINATES) is True
            and self.latitude is not None
            and self.longitude is not None
        )

    @property
    def latitude(self) -> float | None:
        return (
            self.snapshot.status.latitude
            if self.snapshot
            and self.entry.options.get(CONF_COORDINATES, DEFAULT_COORDINATES) is True
            else None
        )

    @property
    def longitude(self) -> float | None:
        return (
            self.snapshot.status.longitude
            if self.snapshot
            and self.entry.options.get(CONF_COORDINATES, DEFAULT_COORDINATES) is True
            else None
        )

    @property
    def entity_picture(self) -> str | None:
        return self.snapshot.profile.image_url if self.snapshot else None

    @property
    def battery_level(self) -> int | None:
        """Associate the tracker with propulsion SOC, never communication SOC."""
        value = self.snapshot.status.battery if self.snapshot else None
        return round(value) if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotTracker(entry, sn)])
