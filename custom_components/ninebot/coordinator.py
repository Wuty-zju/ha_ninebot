"""DataUpdateCoordinator for Ninebot integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NinebotApiClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .exceptions import NinebotApiError, NinebotAuthError, NinebotConnectionError

_LOGGER = logging.getLogger(__name__)


class NinebotDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator that aggregates all devices and state in one polling run."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry,
        api_client: NinebotApiClient,
    ) -> None:
        scan_interval = int(config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.config_entry = config_entry
        self.api_client = api_client

    async def _async_setup(self) -> None:
        """Prime authentication for newer Home Assistant coordinator lifecycle."""
        await self.api_client.async_ensure_token()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all data from Ninebot cloud in one refresh cycle."""
        try:
            await self.api_client.async_ensure_token()
            devices = await self.api_client.async_get_devices()
        except NinebotAuthError as err:
            raise ConfigEntryAuthFailed(f"Ninebot authentication failed: {err}") from err
        except NinebotConnectionError as err:
            raise UpdateFailed(f"Unable to connect to Ninebot cloud: {err}") from err
        except NinebotApiError as err:
            raise UpdateFailed(f"Ninebot API error: {err}") from err

        merged: dict[str, dict[str, Any]] = {}
        for device in devices:
            sn = str(device.get("sn") or "").strip()
            if not sn:
                continue

            try:
                state = await self.api_client.async_get_device_dynamic_info(sn)
            except NinebotAuthError as err:
                raise ConfigEntryAuthFailed(f"Ninebot authentication failed: {err}") from err
            except NinebotConnectionError as err:
                raise UpdateFailed(f"Unable to connect to Ninebot cloud: {err}") from err
            except NinebotApiError as err:
                raise UpdateFailed(f"Ninebot API error: {err}") from err

            merged[sn] = {
                "device": {
                    "sn": sn,
                    "deviceName": device.get("deviceName") or sn,
                    "img": device.get("img"),
                    "model": device.get("model") or device.get("productName") or "",
                },
                "state": state,
            }

        return merged
