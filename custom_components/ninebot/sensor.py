"""Sensor platform for Ninebot integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    STATUS_LOCKED,
    STATUS_UNLOCKED,
    lock_status_from_state,
)
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


def _as_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _as_int(value: Any) -> int | None:
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


def _rssi_dbm_from_csq(value: Any) -> int | None:
    csq = _as_int(value)
    if csq is None:
        return None
    if csq < 0 or csq > 31:
        return None
    return -113 + (2 * csq)


def _location_desc(state: dict[str, Any]) -> str | None:
    location = state.get("locationInfo")
    if isinstance(location, dict):
        desc = location.get("locationDesc")
        return _as_text(desc)
    return None


def _report_time_utc(state: dict[str, Any]) -> datetime | None:
    ts = state.get("gsmTime")
    if isinstance(ts, (int, float)) and ts > 0:
        return dt_util.utc_from_timestamp(float(ts))
    return None


def _vehicle_lock_raw_text(value: Any) -> str | None:
    status = _as_int(value)
    if status == STATUS_LOCKED:
        return "上锁"
    if status == STATUS_UNLOCKED:
        return "已解锁"
    return None


@dataclass(frozen=True, kw_only=True)
class NinebotSensorDescription(SensorEntityDescription):
    """Describes Ninebot sensor entity behavior."""

    value_fn: Callable[[dict[str, Any], dict[str, Any], str], Any]


SENSOR_DESCRIPTIONS: tuple[NinebotSensorDescription, ...] = (
    NinebotSensorDescription(
        key="battery",
        translation_key="battery",
        icon="mdi:battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda state, _device, _sn: _as_int(state.get("battery")),
    ),
    NinebotSensorDescription(
        key="device_name",
        translation_key="device_name",
        icon="mdi:card-text",
        value_fn=lambda _state, device, _sn: _as_text(device.get("device_name")),
    ),
    NinebotSensorDescription(
        key="sn",
        translation_key="sn",
        icon="mdi:barcode",
        value_fn=lambda _state, _device, sn: sn,
    ),
    NinebotSensorDescription(
        key="vehicle_lock_raw",
        translation_key="vehicle_lock_raw",
        icon="mdi:lock-clock",
        value_fn=lambda state, _device, _sn: lock_status_from_state(state),
    ),
    NinebotSensorDescription(
        key="gsm_csq",
        translation_key="gsm_csq",
        icon="mdi:signal",
        value_fn=lambda state, _device, _sn: _as_int(state.get("gsm")),
    ),
    NinebotSensorDescription(
        key="gsm_rssi",
        translation_key="gsm_rssi",
        icon="mdi:wifi",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        value_fn=lambda state, _device, _sn: _rssi_dbm_from_csq(state.get("gsm")),
    ),
    NinebotSensorDescription(
        key="remaining_range",
        translation_key="remaining_range",
        icon="mdi:map-marker-distance",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda state, _device, _sn: state.get("estimateMileage"),
    ),
    NinebotSensorDescription(
        key="remaining_charge_time",
        translation_key="remaining_charge_time",
        icon="mdi:timer-sand",
        value_fn=lambda state, _device, _sn: _as_text(state.get("remainChargeTime")),
    ),
    NinebotSensorDescription(
        key="gsm_report_timestamp",
        translation_key="gsm_report_timestamp",
        icon="mdi:clock-outline",
        value_fn=lambda state, _device, _sn: _as_int(state.get("gsmTime")),
    ),
    NinebotSensorDescription(
        key="gsm_report_time",
        translation_key="gsm_report_time",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda state, _device, _sn: _report_time_utc(state),
    ),
    NinebotSensorDescription(
        key="location",
        translation_key="location",
        icon="mdi:map-marker",
        value_fn=lambda state, _device, _sn: _location_desc(state),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ninebot sensors based on coordinator data."""
    coordinator: NinebotDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities: list[NinebotSensor] = []
    for sn in coordinator.data:
        for description in SENSOR_DESCRIPTIONS:
            entities.append(NinebotSensor(coordinator, sn, description))

    async_add_entities(entities)


class NinebotSensor(NinebotCoordinatorEntity, SensorEntity):
    """Ninebot sensor entity."""

    entity_description: NinebotSensorDescription

    def __init__(
        self,
        coordinator: NinebotDataUpdateCoordinator,
        sn: str,
        description: NinebotSensorDescription,
    ) -> None:
        super().__init__(coordinator, sn)
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_unique_id = self._build_unique_id(description.key)
        self._attr_suggested_object_id = self._build_object_id(description.key)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._state, self._device, self._sn)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "vehicle_lock_raw":
            return None

        status = lock_status_from_state(self._state)
        return {
            "status_text": _vehicle_lock_raw_text(status),
            "status_text_en": "Locked" if status == STATUS_LOCKED else "Unlocked" if status == STATUS_UNLOCKED else None,
        }
