"""Sensor platform for Ninebot integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


def _raw_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _rssi_dbm_from_csq(value: Any) -> int | None:
    if not isinstance(value, int):
        return None
    if value < 0 or value > 31:
        return None
    return -113 + (2 * value)


def _location_desc(state: dict[str, Any]) -> str | None:
    location = state.get("locationInfo")
    if isinstance(location, dict):
        desc = location.get("locationDesc")
        if desc is None:
            return None
        return str(desc)
    return None


def _report_time_local(state: dict[str, Any]) -> datetime | None:
    ts = state.get("gsmTime")
    if isinstance(ts, (int, float)):
        utc_dt = dt_util.utc_from_timestamp(float(ts))
        return dt_util.as_local(utc_dt)
    return None


@dataclass(frozen=True, kw_only=True)
class NinebotSensorDescription(SensorEntityDescription):
    """Describes Ninebot sensor entity behavior."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSOR_DESCRIPTIONS: tuple[NinebotSensorDescription, ...] = (
    NinebotSensorDescription(
        key="battery",
        translation_key="battery",
        icon="mdi:battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda state: state.get("dumpEnergy"),
    ),
    NinebotSensorDescription(
        key="device_name",
        translation_key="device_name",
        icon="mdi:rename-box",
        value_fn=lambda state: None,
    ),
    NinebotSensorDescription(
        key="sn",
        translation_key="sn",
        icon="mdi:barcode",
        value_fn=lambda state: None,
    ),
    NinebotSensorDescription(
        key="location_info",
        translation_key="location_info",
        icon="mdi:map-marker",
        value_fn=_location_desc,
    ),
    NinebotSensorDescription(
        key="estimate_mileage",
        translation_key="estimate_mileage",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda state: state.get("estimateMileage"),
    ),
    NinebotSensorDescription(
        key="remain_charge_time",
        translation_key="remain_charge_time",
        icon="mdi:timer-sand",
        value_fn=lambda state: _raw_text(state.get("remainChargeTime")),
    ),
    NinebotSensorDescription(
        key="gsm_raw",
        translation_key="gsm_raw",
        icon="mdi:signal",
        value_fn=lambda state: _raw_text(state.get("gsm")),
    ),
    NinebotSensorDescription(
        key="rssi",
        translation_key="rssi",
        icon="mdi:wifi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        value_fn=lambda state: _rssi_dbm_from_csq(state.get("gsm")),
    ),
    NinebotSensorDescription(
        key="lock_status_raw",
        translation_key="status_raw",
        icon="mdi:scooter",
        value_fn=lambda state: _raw_text(state.get("powerStatus")),
    ),
    NinebotSensorDescription(
        key="gsm_time_raw",
        translation_key="gsm_time_raw",
        icon="mdi:clock-outline",
        value_fn=lambda state: _raw_text(state.get("gsmTime")),
    ),
    NinebotSensorDescription(
        key="report_time",
        translation_key="report_time",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_report_time_local,
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
        self._attr_unique_id = f"{sn}_{description.key}"
        self._attr_object_id = f"ninebot_{sn}_{description.key}".lower()

    @property
    def name(self) -> str | None:
        return self.entity_description.name

    @property
    def native_value(self) -> Any:
        if self.entity_description.key == "device_name":
            return self._device.get("deviceName")
        if self.entity_description.key == "sn":
            return self._sn
        return self.entity_description.value_fn(self._state)
