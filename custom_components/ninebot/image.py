"""Optional cloud vehicle image, updated with the vehicle profile."""

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotImage(NinebotEntity, ImageEntity):
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        NinebotEntity.__init__(self, entry, sn, "vehicle_image", "image", "profile")
        ImageEntity.__init__(self, self.coordinator.hass)

    @property
    def image_url(self) -> str | None:
        return self.snapshot.profile.image_url if self.snapshot else None

    @property
    def image_last_updated(self) -> datetime | None:
        return self.coordinator._list_freshness.succeeded_at


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotImage(entry, sn)])
