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
    CONF_CHARGE_POWER_WINDOW_SECONDS,
    CHARGING_ON,
    COORDINATOR_TICK_SECONDS,
    CONF_CHARGING_SCAN_INTERVAL,
    CONF_DEBUG,
    CONF_DEFAULT_SCAN_INTERVAL,
    CONF_DISCHARGE_POWER_WINDOW_SECONDS,
    CONF_DEVICE_INFO_FAILURE_TOLERANCE,
    CONF_DEVICE_LIST_REFRESH_INTERVAL_HOURS,
    CONF_MAX_DEVICE_INFO_CONCURRENCY,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_REFRESH_INTERVAL_HOURS,
    CONF_UNLOCKED_SCAN_INTERVAL,
    DEFAULT_CHARGE_POWER_WINDOW_SECONDS,
    DEFAULT_CHARGING_SCAN_INTERVAL,
    DEFAULT_DEBUG,
    DEFAULT_DISCHARGE_POWER_WINDOW_SECONDS,
    DEFAULT_DEVICE_INFO_FAILURE_TOLERANCE,
    DEFAULT_DEVICE_LIST_REFRESH_INTERVAL_HOURS,
    DEFAULT_MAX_DEVICE_INFO_CONCURRENCY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOKEN_REFRESH_INTERVAL_HOURS,
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


def _normalize_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _clamp_battery_percent(value: float) -> float:
    return max(0.0, min(100.0, value))


class NinebotDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator with account-level cache and per-vehicle polling scheduler."""

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
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=COORDINATOR_TICK_SECONDS),
        )
        self.runtime_storage = NinebotRuntimeStorage(hass, config_entry.entry_id)
        self._raw_polling_payloads: dict[str, dict[str, Any]] = {}
        self._raw_devices_payload: list[dict[str, Any]] = []
        self._vehicle_next_poll_at: dict[str, float] = {}
        self._vehicle_intervals: dict[str, int] = {}

    @property
    def debug_enabled(self) -> bool:
        return bool(self.config_entry.options.get(CONF_DEBUG, self.config_entry.data.get(CONF_DEBUG, DEFAULT_DEBUG)))

    def get_raw_polling_payload(self, sn: str) -> dict[str, Any] | None:
        payload = self._raw_polling_payloads.get(sn)
        if payload is None:
            return None
        return copy.deepcopy(payload)

    def _int_from_entry(self, key: str, *, default: int, minimum: int = 1) -> int:
        raw = self.config_entry.options.get(key, self.config_entry.data.get(key, default))
        if isinstance(raw, bool):
            return max(minimum, default)
        if isinstance(raw, (int, float)):
            return max(minimum, int(raw))
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return max(minimum, default)
            try:
                return max(minimum, int(text))
            except ValueError:
                return max(minimum, default)
        return max(minimum, default)

    @property
    def default_scan_interval(self) -> int:
        return self._int_from_entry(
            CONF_DEFAULT_SCAN_INTERVAL,
            default=int(self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            minimum=1,
        )

    @property
    def unlocked_scan_interval(self) -> int:
        return self._int_from_entry(CONF_UNLOCKED_SCAN_INTERVAL, default=DEFAULT_UNLOCKED_SCAN_INTERVAL, minimum=1)

    @property
    def charging_scan_interval(self) -> int:
        return self._int_from_entry(CONF_CHARGING_SCAN_INTERVAL, default=DEFAULT_CHARGING_SCAN_INTERVAL, minimum=1)

    @property
    def discharge_power_window_seconds(self) -> int:
        return self._int_from_entry(
            CONF_DISCHARGE_POWER_WINDOW_SECONDS,
            default=DEFAULT_DISCHARGE_POWER_WINDOW_SECONDS,
            minimum=1,
        )

    @property
    def charge_power_window_seconds(self) -> int:
        return self._int_from_entry(
            CONF_CHARGE_POWER_WINDOW_SECONDS,
            default=DEFAULT_CHARGE_POWER_WINDOW_SECONDS,
            minimum=1,
        )

    @property
    def token_refresh_interval_hours(self) -> int:
        return self._int_from_entry(CONF_TOKEN_REFRESH_INTERVAL_HOURS, default=DEFAULT_TOKEN_REFRESH_INTERVAL_HOURS, minimum=1)

    @property
    def device_list_refresh_interval_hours(self) -> int:
        return self._int_from_entry(
            CONF_DEVICE_LIST_REFRESH_INTERVAL_HOURS,
            default=DEFAULT_DEVICE_LIST_REFRESH_INTERVAL_HOURS,
            minimum=1,
        )

    @property
    def max_device_info_concurrency(self) -> int:
        return self._int_from_entry(CONF_MAX_DEVICE_INFO_CONCURRENCY, default=DEFAULT_MAX_DEVICE_INFO_CONCURRENCY, minimum=1)

    @property
    def device_info_failure_tolerance(self) -> int:
        return self._int_from_entry(
            CONF_DEVICE_INFO_FAILURE_TOLERANCE,
            default=DEFAULT_DEVICE_INFO_FAILURE_TOLERANCE,
            minimum=0,
        )

    def _interval_mode_for_state(self, state: dict[str, Any]) -> tuple[str, int]:
        lock_state = status_to_locked(_normalize_int(state.get("status")))
        if lock_state is False:
            return "unlocked", self.unlocked_scan_interval
        if _normalize_int(state.get("chargingState")) == CHARGING_ON:
            return "charging", self.charging_scan_interval
        return "default", self.default_scan_interval

    @staticmethod
    def _normalize_state(raw_state: dict[str, Any]) -> dict[str, Any]:
        location = raw_state.get("locationInfo")
        if not isinstance(location, dict):
            location = {}
        return {
            "battery": _normalize_int(_first_present(raw_state.get("battery"), raw_state.get("dumpEnergy"))),
            # Keep raw lock status semantics for all entity layers: 0=locked, 1=unlocked.
            "status": _normalize_int(_first_present(raw_state.get("status"), raw_state.get("powerStatus"))),
            "chargingState": _normalize_int(raw_state.get("chargingState")),
            "pwr": _normalize_int(raw_state.get("pwr")),
            "gsm": _normalize_int(raw_state.get("gsm")),
            "estimateMileage": _first_present(raw_state.get("estimateMileage"), raw_state.get("mileage")),
            "remainChargeTime": raw_state.get("remainChargeTime"),
            "gsmTime": _normalize_int(raw_state.get("gsmTime")),
            "locationInfo": location,
        }

    @staticmethod
    def _calculate_battery_percent(*, remaining_range_km: float | None, max_range_km: float | None) -> float | None:
        """Derive battery percentage from remaining/max range with safe guards.

        Formula: battery_calculated = remaining_range_km / max_range_km * 100.
        """
        if not isinstance(remaining_range_km, (int, float)):
            return None
        if not isinstance(max_range_km, (int, float)) or float(max_range_km) <= 0:
            return None
        computed = float(remaining_range_km) / float(max_range_km) * 100.0
        return _clamp_battery_percent(computed)

    @staticmethod
    def _build_device_meta(device: dict[str, Any], raw_state: dict[str, Any], sn: str) -> dict[str, Any]:
        return {
            "sn": sn,
            "device_name": str(_first_present(device.get("deviceName"), device.get("name"), raw_state.get("deviceName"), sn)),
            "img": _first_present(device.get("img"), raw_state.get("img")),
            "model": device.get("model") or device.get("productName") or "",
        }

    @staticmethod
    def _should_force_refresh_devices_from_error(err: Exception) -> bool:
        text = str(err).lower()
        return any(keyword in text for keyword in ("device", "sn", "not found", "mismatch", "account"))

    async def _async_setup(self) -> None:
        """Prime auth/device caches on startup."""
        await self.api_client.async_load_cached_auth()
        await self.api_client.async_load_cached_devices()
        await self.api_client.async_ensure_token(refresh_interval_hours=self.token_refresh_interval_hours)
        try:
            devices = await self.api_client.async_get_devices(
                force_refresh=False,
                refresh_interval_hours=self.device_list_refresh_interval_hours,
                token_refresh_interval_hours=self.token_refresh_interval_hours,
            )
            sns = [str(device.get("sn") or "").strip() for device in devices if str(device.get("sn") or "").strip()]
        except Exception:  # noqa: BLE001 - continue with persisted cache
            sns = []

        await self.runtime_storage.async_initialize(sns)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Run scheduler tick: refresh due vehicles only."""
        now_dt = dt_util.utcnow()
        now_ts = float(now_dt.timestamp())

        try:
            await self.api_client.async_ensure_token(refresh_interval_hours=self.token_refresh_interval_hours)
            devices = await self.api_client.async_get_devices(
                force_refresh=False,
                refresh_interval_hours=self.device_list_refresh_interval_hours,
                token_refresh_interval_hours=self.token_refresh_interval_hours,
            )
        except NinebotAuthError as err:
            raise ConfigEntryAuthFailed(f"Ninebot authentication failed: {err}") from err
        except NinebotConnectionError as err:
            raise UpdateFailed(f"Unable to connect to Ninebot cloud: {err}") from err
        except NinebotApiError as err:
            raise UpdateFailed(f"Ninebot API error: {err}") from err

        self._raw_devices_payload = copy.deepcopy(devices) if self.debug_enabled else []

        device_by_sn: dict[str, dict[str, Any]] = {}
        for device in devices:
            sn = str(device.get("sn") or "").strip()
            if sn:
                device_by_sn[sn] = device

        sns = list(device_by_sn.keys())
        await self.runtime_storage.async_initialize(sns)

        existing_data = self.data if isinstance(self.data, dict) else {}
        merged: dict[str, dict[str, Any]] = {}
        for sn in sns:
            device = device_by_sn[sn]
            current_item = existing_data.get(sn)
            previous_state: dict[str, Any] | None = None
            if isinstance(current_item, dict):
                state = current_item.get("state")
                if isinstance(state, dict):
                    previous_state = dict(state)
            if previous_state is None:
                previous_state = self.runtime_storage.get_cached_vehicle_state(sn)
            if previous_state is None:
                previous_state = {}

            mode, interval = self._interval_mode_for_state(previous_state)
            self._vehicle_intervals[sn] = interval
            self._vehicle_next_poll_at.setdefault(sn, 0.0)

            metadata = self.runtime_storage.get_vehicle_metadata(sn)
            previous_state.setdefault("polling_mode", mode)
            previous_state.setdefault("current_scan_interval", interval)
            previous_state.setdefault("last_success_at", metadata.get("last_success_at"))
            previous_state.setdefault("last_attempt_at", metadata.get("last_attempt_at"))
            previous_state.setdefault("failure_count", metadata.get("failure_count"))
            previous_state.setdefault("data_source", "cached")

            merged[sn] = {
                "device": self._build_device_meta(device, previous_state, sn),
                "state": previous_state,
            }

        for sn in list(self._vehicle_next_poll_at):
            if sn not in device_by_sn:
                self._vehicle_next_poll_at.pop(sn, None)
                self._vehicle_intervals.pop(sn, None)

        for sn in list(self._raw_polling_payloads):
            if sn not in device_by_sn:
                self._raw_polling_payloads.pop(sn, None)

        due_sns = [sn for sn in sns if now_ts >= float(self._vehicle_next_poll_at.get(sn, 0.0))]
        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, Exception] = {}

        if due_sns:
            results, errors = await self.api_client.async_get_multiple_device_dynamic_info(
                due_sns,
                max_concurrency=self.max_device_info_concurrency,
                token_refresh_interval_hours=self.token_refresh_interval_hours,
            )

        if errors and any(self._should_force_refresh_devices_from_error(err) for err in errors.values()):
            try:
                await self.api_client.async_get_devices(
                    force_refresh=True,
                    refresh_interval_hours=self.device_list_refresh_interval_hours,
                    token_refresh_interval_hours=self.token_refresh_interval_hours,
                )
            except Exception:  # noqa: BLE001 - keep current cycle data
                pass

        for sn in due_sns:
            device = device_by_sn.get(sn)
            if not isinstance(device, dict):
                continue

            if sn in results:
                raw_state = results[sn]
                state = self._normalize_state(raw_state)

                # All power/energy estimation is intentionally bound to the
                # derived battery percentage from remaining range + max range,
                # instead of the cloud-reported raw battery percentage.
                remaining_range_km = _normalize_float(state.get("estimateMileage"))
                max_range_km = await self.runtime_storage.async_resolve_battery_max_range(
                    sn,
                    remaining_range_km=remaining_range_km,
                    raw_battery_percent=state["battery"],
                    persist=False,
                )
                state["battery_max_range"] = round(max_range_km, 3)
                battery_calculated = self._calculate_battery_percent(
                    remaining_range_km=remaining_range_km,
                    max_range_km=max_range_km,
                )
                state["battery_calculated"] = round(battery_calculated, 3) if battery_calculated is not None else None

                state.update(
                    await self.runtime_storage.async_build_energy_snapshot(
                        sn,
                        battery_percent=state["battery_calculated"],
                        sample_time=now_dt,
                        discharge_window_seconds=self.discharge_power_window_seconds,
                        charge_window_seconds=self.charge_power_window_seconds,
                        persist=False,
                    )
                )
                voltage, capacity = self.runtime_storage.get_battery_params(sn)
                state["main_battery_voltage"] = round(voltage, 3)
                state["battery_capacity"] = round(capacity, 3)

                mode, interval = self._interval_mode_for_state(state)
                self._vehicle_intervals[sn] = interval
                self._vehicle_next_poll_at[sn] = now_ts + interval

                state["polling_mode"] = mode
                state["current_scan_interval"] = interval
                state["data_source"] = "live"

                await self.runtime_storage.async_record_vehicle_success(
                    sn,
                    raw_state=raw_state,
                    parsed_state=state,
                    now_ts=now_ts,
                    current_interval=interval,
                    save=True,
                )
                metadata = self.runtime_storage.get_vehicle_metadata(sn)
                state["last_success_at"] = metadata.get("last_success_at")
                state["last_attempt_at"] = metadata.get("last_attempt_at")
                state["failure_count"] = metadata.get("failure_count")

                merged[sn] = {
                    "device": self._build_device_meta(device, raw_state, sn),
                    "state": state,
                }

                if self.debug_enabled:
                    self._raw_polling_payloads[sn] = {
                        "fetched_at": now_dt.isoformat(),
                        "device_list_item": copy.deepcopy(device),
                        "dynamic_info": copy.deepcopy(raw_state),
                        "devices_list_raw": self._raw_devices_payload,
                    }
                continue

            err = errors.get(sn)
            cached_state = merged.get(sn, {}).get("state")
            if not isinstance(cached_state, dict):
                cached_state = self.runtime_storage.get_cached_vehicle_state(sn) or {}

            mode, interval = self._interval_mode_for_state(cached_state)
            self._vehicle_intervals[sn] = interval
            self._vehicle_next_poll_at[sn] = now_ts + interval
            failure_count = await self.runtime_storage.async_record_vehicle_failure(
                sn,
                now_ts=now_ts,
                current_interval=interval,
            )

            state_for_merge = dict(cached_state)
            if not state_for_merge and failure_count > self.device_info_failure_tolerance:
                state_for_merge["available"] = False

            state_for_merge["polling_mode"] = mode
            state_for_merge["current_scan_interval"] = interval
            state_for_merge["data_source"] = "cached"
            state_for_merge["last_error"] = str(err) if err is not None else None
            metadata = self.runtime_storage.get_vehicle_metadata(sn)
            state_for_merge["last_success_at"] = metadata.get("last_success_at")
            state_for_merge["last_attempt_at"] = metadata.get("last_attempt_at")
            state_for_merge["failure_count"] = metadata.get("failure_count")

            merged[sn]["state"] = state_for_merge

            if self.debug_enabled:
                self._raw_polling_payloads[sn] = {
                    "fetched_at": now_dt.isoformat(),
                    "device_list_item": copy.deepcopy(device),
                    "dynamic_info": None,
                    "error": str(err) if err is not None else None,
                    "devices_list_raw": self._raw_devices_payload,
                }

        for sn, item in merged.items():
            state = item.get("state")
            if not isinstance(state, dict):
                state = {}
                item["state"] = state
            state.setdefault("current_scan_interval", int(self._vehicle_intervals.get(sn, self.default_scan_interval)))
            state.setdefault("polling_mode", "default")
            state.setdefault("data_source", "cached")
            state.setdefault("failure_count", int(self.runtime_storage.get_vehicle_metadata(sn).get("failure_count") or 0))

        return merged

    async def async_set_main_battery_voltage(self, sn: str, value: float) -> None:
        await self.runtime_storage.async_set_battery_param(sn, voltage=value)
        await self.async_request_refresh()

    async def async_set_battery_capacity(self, sn: str, value: float) -> None:
        await self.runtime_storage.async_set_battery_param(sn, capacity=value)
        await self.async_request_refresh()

    async def async_set_battery_max_range(self, sn: str, value: float) -> None:
        await self.runtime_storage.async_set_battery_max_range(sn, value)
        await self.async_request_refresh()
