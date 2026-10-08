"""Public cloud vehicle image, updated with the vehicle profile."""

import logging

import httpx
from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import NinebotEntity, async_setup_dynamic
from .image_urls import public_image_url
from .runtime import NinebotConfigEntry

LOGGER = logging.getLogger(__name__)


class NinebotImage(NinebotEntity, ImageEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        NinebotEntity.__init__(self, entry, sn, "vehicle_image", "image", "profile")
        ImageEntity.__init__(self, self.coordinator.hass, verify_ssl=True)
        self._profile_image_url = self.snapshot.profile.image_url if self.snapshot else None
        self._attr_image_last_updated = (
            self.coordinator._list_freshness.succeeded_at if self._profile_image_url else None
        )

    @property
    def available(self) -> bool:
        return super().available and self.image_url is not None

    @property
    def image_url(self) -> str | None:
        return self._profile_image_url

    @callback
    def _handle_coordinator_update(self) -> None:
        url = self.snapshot.profile.image_url if self.snapshot else None
        if url != self._profile_image_url:
            self._profile_image_url = url
            self._cached_image = None
            self._attr_image_last_updated = (
                self.coordinator._list_freshness.succeeded_at if url else None
            )
        super()._handle_coordinator_update()

    async def _fetch_url(self, url: str) -> httpx.Response | None:
        """Keep the official image decoder/cache, with a constrained HTTP hook.

        Core's default hook follows redirects and logs complete failed URLs.
        Reject redirects before contacting another origin and never log URLs or
        exception text. Shared HA HTTP settings are not modified.
        """
        if public_image_url(url) != url:
            return None
        try:
            response = await self._client.get(url, timeout=10, follow_redirects=False)
            response.raise_for_status()
        except httpx.HTTPError:
            LOGGER.debug("Vehicle image could not be fetched")
            return None
        return response if response.status_code == 200 else None


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotImage(entry, sn)])
