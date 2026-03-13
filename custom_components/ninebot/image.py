"""Image platform for Ninebot integration."""

from __future__ import annotations

from collections import deque
import logging
from secrets import token_hex

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
    entities = [NinebotVehicleImage(hass, coordinator, sn, session) for sn in coordinator.data]
    async_add_entities(entities)


class NinebotVehicleImage(NinebotCoordinatorEntity, ImageEntity):
    """Vehicle image entity loaded from Ninebot device img URL."""

    _attr_translation_key = "vehicle_image"
    _attr_icon = "mdi:image"
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NinebotDataUpdateCoordinator,
        sn: str,
        session,
    ) -> None:
        ImageEntity.__init__(self, hass)
        NinebotCoordinatorEntity.__init__(self, coordinator, sn)
        self._session = session
        self._attr_unique_id = self._build_unique_id("vehicle_image")
        self._attr_suggested_object_id = self._build_object_id("vehicle_image")
        self._attr_content_type = "image/png"
        # Defensive fallback for older/newer core behaviors where ImageEntity init chain differs.
        if not hasattr(self, "access_tokens"):
            self.access_tokens = deque([token_hex(16)], maxlen=2)

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
            async with self._session.get(url, allow_redirects=True, timeout=20) as resp:
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
