"""DataUpdateCoordinator for Ninebot integration."""

from __future__ import annotations

import copy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NinebotApiClient
from .const import (
    CHARGING_ON,
    CONF_CHARGING_SCAN_INTERVAL,
    CONF_DEBUG,
    CONF_DEFAULT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_UNLOCKED_SCAN_INTERVAL,
    DEFAULT_CHARGING_SCAN_INTERVAL,
    DEFAULT_DEBUG,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNLOCKED_SCAN_INTERVAL,
    DOMAIN,
    status_to_locked,
)
from .exceptions import NinebotApiError, NinebotAuthError, NinebotConnectionError
from .storage import NinebotRuntimeStorage

_LOGGER = logging.getLogger(__name__)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _normalize_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


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
        self.config_entry = config_entry
        self.api_client = api_client
        scan_interval = self._interval_from_entry(
            CONF_DEFAULT_SCAN_INTERVAL,
            default=int(config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.runtime_storage = NinebotRuntimeStorage(hass, config_entry.entry_id)
        self._polling_mode = "default"
        self._raw_polling_payloads: dict[str, dict[str, Any]] = {}
        self._raw_devices_payload: list[dict[str, Any]] = []

    @property
    def debug_enabled(self) -> bool:
        return bool(self.config_entry.options.get(CONF_DEBUG, self.config_entry.data.get(CONF_DEBUG, DEFAULT_DEBUG)))

    def get_raw_polling_payload(self, sn: str) -> dict[str, Any] | None:
        payload = self._raw_polling_payloads.get(sn)
        if payload is None:
            return None
        return copy.deepcopy(payload)

    def _interval_from_entry(self, key: str, *, default: int) -> int:
        raw = self.config_entry.options.get(key, self.config_entry.data.get(key, default))
        if isinstance(raw, bool):
            return default
        if isinstance(raw, (int, float)):
            return max(1, int(raw))
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return default
            try:
                return max(1, int(text))
            except ValueError:
                return default
        return default

    @property
    def default_scan_interval(self) -> int:
        return self._interval_from_entry(CONF_DEFAULT_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL)

    @property
    def unlocked_scan_interval(self) -> int:
        return self._interval_from_entry(CONF_UNLOCKED_SCAN_INTERVAL, default=DEFAULT_UNLOCKED_SCAN_INTERVAL)

    @property
    def charging_scan_interval(self) -> int:
        return self._interval_from_entry(CONF_CHARGING_SCAN_INTERVAL, default=DEFAULT_CHARGING_SCAN_INTERVAL)

    @property
    def polling_mode(self) -> str:
        return self._polling_mode

    def _recalculate_update_interval(self, merged: dict[str, dict[str, Any]]) -> None:
        has_unlocked = False
        has_charging = False

        for item in merged.values():
            state = item.get("state") if isinstance(item, dict) else None
            if not isinstance(state, dict):
                continue

            lock_state = status_to_locked(_normalize_int(state.get("status")))
            if lock_state is False:
                has_unlocked = True

            if _normalize_int(state.get("chargingState")) == CHARGING_ON:
                has_charging = True

        if has_unlocked:
            new_mode = "unlocked"
            new_interval = self.unlocked_scan_interval
        elif has_charging:
            new_mode = "charging"
            new_interval = self.charging_scan_interval
        else:
            new_mode = "default"
            new_interval = self.default_scan_interval

        self._polling_mode = new_mode
        self.update_interval = timedelta(seconds=new_interval)

    async def _async_setup(self) -> None:
        """Prime authentication for newer Home Assistant coordinator lifecycle."""
        await self.api_client.async_ensure_token()
        await self.runtime_storage.async_initialize([])

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
        self._raw_polling_payloads = {}
        self._raw_devices_payload = copy.deepcopy(devices) if self.debug_enabled else []
        device_sns = [str(device.get("sn") or "").strip() for device in devices if str(device.get("sn") or "").strip()]
        await self.runtime_storage.async_initialize(device_sns)
        now = dt_util.utcnow()
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

            if self.debug_enabled:
                self._raw_polling_payloads[sn] = {
                    "fetched_at": now.isoformat(),
                    "device_list_item": copy.deepcopy(device),
                    "dynamic_info": copy.deepcopy(state),
                    "devices_list_raw": copy.deepcopy(self._raw_devices_payload),
                }

            location = state.get("locationInfo")
            if not isinstance(location, dict):
                location = {}

            normalized_state = {
                "battery": _normalize_int(_first_present(state.get("battery"), state.get("dumpEnergy"))),
                "status": _normalize_int(_first_present(state.get("status"), state.get("powerStatus"))),
                "chargingState": _normalize_int(state.get("chargingState")),
                "pwr": _normalize_int(state.get("pwr")),
                "gsm": _normalize_int(state.get("gsm")),
                "estimateMileage": _first_present(state.get("estimateMileage"), state.get("mileage")),
                "remainChargeTime": state.get("remainChargeTime"),
                "gsmTime": _normalize_int(state.get("gsmTime")),
                "locationInfo": location,
            }

            energy_snapshot = await self.runtime_storage.async_build_energy_snapshot(
                sn,
                battery_percent=normalized_state["battery"],
                sample_time=now,
            )
            normalized_state.update(energy_snapshot)

            voltage, capacity = self.runtime_storage.get_battery_params(sn)
            normalized_state["main_battery_voltage"] = round(voltage, 3)
            normalized_state["battery_capacity"] = round(capacity, 3)
            normalized_state["current_scan_interval"] = int(self.update_interval.total_seconds())
            normalized_state["polling_mode"] = self.polling_mode

            device_name = str(
                _first_present(
                    device.get("deviceName"),
                    device.get("name"),
                    state.get("deviceName"),
                    sn,
                )
            )

            merged[sn] = {
                "device": {
                    "sn": sn,
                    "device_name": device_name,
                    "img": _first_present(device.get("img"), state.get("img")),
                    "model": device.get("model") or device.get("productName") or "",
                },
                "state": normalized_state,
            }


        self._recalculate_update_interval(merged)

        for item in merged.values():
            state = item.get("state")
            if not isinstance(state, dict):
                continue
            state["current_scan_interval"] = int(self.update_interval.total_seconds())
            state["polling_mode"] = self.polling_mode
        return merged


    async def async_set_main_battery_voltage(self, sn: str, value: float) -> None:
        await self.runtime_storage.async_set_battery_param(sn, voltage=value)
        await self.async_request_refresh()

    async def async_set_battery_capacity(self, sn: str, value: float) -> None:
        await self.runtime_storage.async_set_battery_param(sn, capacity=value)
        await self.async_request_refresh()
