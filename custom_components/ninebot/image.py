"""Image platform for Ninebot integration."""

from __future__ import annotations

import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ninebot image entities."""
    coordinator: NinebotDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    session = async_get_clientsession(hass)
    entities = [NinebotVehicleImage(coordinator, sn, session) for sn in coordinator.data]
    async_add_entities(entities)


class NinebotVehicleImage(NinebotCoordinatorEntity, ImageEntity):
    """Vehicle image entity loaded from Ninebot device img URL."""

    _attr_translation_key = "vehicle_image"
    _attr_icon = "mdi:image-outline"
    _attr_has_entity_name = True

    def __init__(self, coordinator: NinebotDataUpdateCoordinator, sn: str, session) -> None:
        super().__init__(coordinator, sn)
        self._session = session
        self._attr_unique_id = f"{sn}_vehicle_image"
        self._attr_content_type = "image/png"

    @property
    def image_url(self) -> str | None:
        img = self._device.get("img")
        if isinstance(img, str) and img:
            return img
        return None

    async def async_image(self) -> bytes | None:
        url = self.image_url
        if not url:
            return None
        try:
            async with self._session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Ninebot image request failed status=%s url=%s", resp.status, url)
                    return None
                content_type = resp.headers.get("Content-Type")
                if content_type:
                    self._attr_content_type = content_type
                return await resp.read()
        except Exception as err:  # pragma: no cover - network dependent
            _LOGGER.debug("Ninebot image request failed: %s", err)
            return None
