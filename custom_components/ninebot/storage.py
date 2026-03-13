"""Runtime storage helpers for the Ninebot integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DEFAULT_BATTERY_CAPACITY, DEFAULT_MAIN_BATTERY_VOLTAGE, DOMAIN

_STORAGE_VERSION = 2


class _NinebotStore(Store[dict[str, Any]]):
    """Store wrapper with backward-compatible migration support."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        del old_major_version, old_minor_version

        if not isinstance(old_data, dict):
            return {"devices": {}}

        devices = old_data.get("devices")
        if isinstance(devices, dict):
            return {"devices": devices}

        # Legacy layouts may store per-device payloads directly at root.
        legacy_devices: dict[str, dict[str, Any]] = {}
        for sn, payload in old_data.items():
            if isinstance(payload, dict):
                legacy_devices[str(sn)] = payload
        return {"devices": legacy_devices}


class NinebotRuntimeStorage:
    """Persist per-device parameters, energy counters and last successful states."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = _NinebotStore(
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}_{entry_id}_runtime",
        )
        self._data: dict[str, Any] = {"devices": {}}
        self._loaded = False

    async def async_initialize(self, sns: list[str]) -> None:
        if not self._loaded:
            loaded = await self._store.async_load()
            if isinstance(loaded, dict):
                self._data = loaded
            if not isinstance(self._data.get("devices"), dict):
                self._data["devices"] = {}
            self._loaded = True

        dirty = False
        for sn in sns:
            if self._ensure_device(sn):
                dirty = True
        if dirty:
            await self.async_save()

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    def _ensure_device(self, sn: str) -> bool:
        devices = self._data["devices"]
        if not isinstance(devices, dict):
            self._data["devices"] = {}
            devices = self._data["devices"]

        existing = devices.get(sn)
        if isinstance(existing, dict):
            dirty = False
            dirty |= self._ensure_float(existing, "main_battery_voltage", DEFAULT_MAIN_BATTERY_VOLTAGE)
            dirty |= self._ensure_float(existing, "battery_capacity", DEFAULT_BATTERY_CAPACITY)
            dirty |= self._ensure_optional_float(existing, "last_battery_percent")
            dirty |= self._ensure_optional_float(existing, "last_sample_ts")
            dirty |= self._ensure_float(existing, "inflow_total_kwh", 0.0)
            dirty |= self._ensure_float(existing, "outflow_total_kwh", 0.0)
            dirty |= self._ensure_float(existing, "daily_inflow_kwh", 0.0)
            dirty |= self._ensure_float(existing, "daily_outflow_kwh", 0.0)
            dirty |= self._ensure_float(existing, "monthly_inflow_kwh", 0.0)
            dirty |= self._ensure_float(existing, "monthly_outflow_kwh", 0.0)
            dirty |= self._ensure_int(existing, "failure_count", 0)
            dirty |= self._ensure_int(existing, "current_interval", 60)
            dirty |= self._ensure_optional_float(existing, "last_success_at")
            dirty |= self._ensure_optional_float(existing, "last_attempt_at")
            if not isinstance(existing.get("daily_bucket"), str):
                existing["daily_bucket"] = ""
                dirty = True
            if not isinstance(existing.get("monthly_bucket"), str):
                existing["monthly_bucket"] = ""
                dirty = True
            if existing.get("raw_state") is not None and not isinstance(existing.get("raw_state"), dict):
                existing["raw_state"] = None
                dirty = True
            if existing.get("parsed_state") is not None and not isinstance(existing.get("parsed_state"), dict):
                existing["parsed_state"] = None
                dirty = True
            return dirty

        devices[sn] = {
            "main_battery_voltage": DEFAULT_MAIN_BATTERY_VOLTAGE,
            "battery_capacity": DEFAULT_BATTERY_CAPACITY,
            "last_battery_percent": None,
            "last_sample_ts": None,
            "inflow_total_kwh": 0.0,
            "outflow_total_kwh": 0.0,
            "daily_inflow_kwh": 0.0,
            "daily_outflow_kwh": 0.0,
            "monthly_inflow_kwh": 0.0,
            "monthly_outflow_kwh": 0.0,
            "daily_bucket": "",
            "monthly_bucket": "",
            "raw_state": None,
            "parsed_state": None,
            "last_success_at": None,
            "last_attempt_at": None,
            "failure_count": 0,
            "current_interval": 60,
        }
        return True

    def _device(self, sn: str) -> dict[str, Any]:
        self._ensure_device(sn)
        return self._data["devices"][sn]

    @staticmethod
    def _ensure_float(data: dict[str, Any], key: str, default: float) -> bool:
        value = data.get(key)
        if isinstance(value, (int, float)):
            data[key] = float(value)
            return False
        data[key] = float(default)
        return True

    @staticmethod
    def _ensure_int(data: dict[str, Any], key: str, default: int) -> bool:
        value = data.get(key)
        if isinstance(value, bool):
            data[key] = int(default)
            return True
        if isinstance(value, int):
            return False
        if isinstance(value, float):
            data[key] = int(value)
            return True
        data[key] = int(default)
        return True

    @staticmethod
    def _ensure_optional_float(data: dict[str, Any], key: str) -> bool:
        value = data.get(key)
        if value is None:
            return False
        if isinstance(value, (int, float)):
            data[key] = float(value)
            return False
        data[key] = None
        return True

    def get_battery_params(self, sn: str) -> tuple[float, float]:
        device = self._device(sn)
        return float(device["main_battery_voltage"]), float(device["battery_capacity"])

    async def async_set_battery_param(self, sn: str, *, voltage: float | None = None, capacity: float | None = None) -> None:
        device = self._device(sn)
        dirty = False
        if voltage is not None and float(voltage) != float(device["main_battery_voltage"]):
            device["main_battery_voltage"] = float(voltage)
            dirty = True
        if capacity is not None and float(capacity) != float(device["battery_capacity"]):
            device["battery_capacity"] = float(capacity)
            dirty = True
        if dirty:
            await self.async_save()

    def get_cached_vehicle_state(self, sn: str) -> dict[str, Any] | None:
        parsed = self._device(sn).get("parsed_state")
        if not isinstance(parsed, dict):
            return None
        return dict(parsed)

    def get_vehicle_metadata(self, sn: str) -> dict[str, Any]:
        device = self._device(sn)
        return {
            "last_success_at": device.get("last_success_at"),
            "last_attempt_at": device.get("last_attempt_at"),
            "failure_count": int(device.get("failure_count") or 0),
            "current_interval": int(device.get("current_interval") or 60),
        }

    async def async_record_vehicle_success(
        self,
        sn: str,
        *,
        raw_state: dict[str, Any],
        parsed_state: dict[str, Any],
        now_ts: float,
        current_interval: int,
    ) -> None:
        device = self._device(sn)
        device["raw_state"] = raw_state
        device["parsed_state"] = parsed_state
        device["last_success_at"] = float(now_ts)
        device["last_attempt_at"] = float(now_ts)
        device["failure_count"] = 0
        device["current_interval"] = int(current_interval)
        await self.async_save()

    async def async_record_vehicle_failure(self, sn: str, *, now_ts: float, current_interval: int) -> int:
        device = self._device(sn)
        device["last_attempt_at"] = float(now_ts)
        device["failure_count"] = int(device.get("failure_count") or 0) + 1
        device["current_interval"] = int(current_interval)
        await self.async_save()
        return int(device["failure_count"])

    async def async_set_vehicle_interval(self, sn: str, interval: int) -> None:
        device = self._device(sn)
        if int(device.get("current_interval") or 60) != int(interval):
            device["current_interval"] = int(interval)
            await self.async_save()

    async def async_build_energy_snapshot(
        self,
        sn: str,
        *,
        battery_percent: int | None,
        sample_time: datetime,
    ) -> dict[str, float | None]:
        device = self._device(sn)

        voltage = float(device["main_battery_voltage"])
        capacity = float(device["battery_capacity"])
        nominal_kwh = (voltage * capacity) / 1000.0

        daily_bucket = sample_time.strftime("%Y-%m-%d")
        monthly_bucket = sample_time.strftime("%Y-%m")

        dirty = False
        if device.get("daily_bucket") != daily_bucket:
            device["daily_bucket"] = daily_bucket
            device["daily_inflow_kwh"] = 0.0
            device["daily_outflow_kwh"] = 0.0
            dirty = True

        if device.get("monthly_bucket") != monthly_bucket:
            device["monthly_bucket"] = monthly_bucket
            device["monthly_inflow_kwh"] = 0.0
            device["monthly_outflow_kwh"] = 0.0
            dirty = True

        delta_kwh = 0.0
        inflow_step = 0.0
        outflow_step = 0.0
        inflow_power = 0.0
        outflow_power = 0.0

        prev_percent = device.get("last_battery_percent")
        prev_ts = device.get("last_sample_ts")

        valid_battery = isinstance(battery_percent, int) and 0 <= battery_percent <= 100
        if valid_battery and isinstance(prev_percent, (int, float)) and isinstance(prev_ts, (int, float)):
            delta_seconds = sample_time.timestamp() - float(prev_ts)
            delta_percent = float(battery_percent) - float(prev_percent)

            if delta_seconds > 0:
                if abs(delta_percent) <= 40 or delta_seconds >= 60:
                    delta_kwh = nominal_kwh * delta_percent / 100.0
                    inflow_step = max(delta_kwh, 0.0)
                    outflow_step = max(-delta_kwh, 0.0)

                    if inflow_step > 0:
                        device["inflow_total_kwh"] = float(device["inflow_total_kwh"]) + inflow_step
                        device["daily_inflow_kwh"] = float(device["daily_inflow_kwh"]) + inflow_step
                        device["monthly_inflow_kwh"] = float(device["monthly_inflow_kwh"]) + inflow_step
                        inflow_power = inflow_step * 3600000.0 / delta_seconds
                        dirty = True

                    if outflow_step > 0:
                        device["outflow_total_kwh"] = float(device["outflow_total_kwh"]) + outflow_step
                        device["daily_outflow_kwh"] = float(device["daily_outflow_kwh"]) + outflow_step
                        device["monthly_outflow_kwh"] = float(device["monthly_outflow_kwh"]) + outflow_step
                        outflow_power = outflow_step * 3600000.0 / delta_seconds
                        dirty = True

        if valid_battery:
            device["last_battery_percent"] = float(battery_percent)
            device["last_sample_ts"] = float(sample_time.timestamp())
            dirty = True

        if dirty:
            await self.async_save()

        return {
            "battery_nominal_energy": round(nominal_kwh, 6),
            "battery_energy_delta": round(delta_kwh, 6),
            "battery_inflow_energy_step": round(inflow_step, 6),
            "battery_outflow_energy_step": round(outflow_step, 6),
            "battery_inflow_power": round(inflow_power, 3),
            "battery_outflow_power": round(outflow_power, 3),
            "battery_inflow_energy_daily": round(float(device["daily_inflow_kwh"]), 6),
            "battery_outflow_energy_daily": round(float(device["daily_outflow_kwh"]), 6),
            "battery_inflow_energy_monthly": round(float(device["monthly_inflow_kwh"]), 6),
            "battery_outflow_energy_monthly": round(float(device["monthly_outflow_kwh"]), 6),
        }
