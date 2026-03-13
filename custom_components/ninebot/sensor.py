"""Sensor platform for Ninebot integration."""

from __future__ import annotations

from dataclasses import dataclass
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

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


def _enum_from_map(value: Any, mapping: dict[int, str]) -> str | None:
    if not isinstance(value, int):
        return None
    return mapping.get(value, str(value))


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
        key="estimate_mileage",
        translation_key="estimate_mileage",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda state: state.get("estimateMileage"),
    ),
    NinebotSensorDescription(
        key="gsm",
        translation_key="gsm",
        icon="mdi:signal",
        value_fn=lambda state: state.get("gsm"),
    ),
    NinebotSensorDescription(
        key="charging_state",
        translation_key="charging_state",
        icon="mdi:battery-charging",
        device_class=SensorDeviceClass.ENUM,
        options=["not_charging", "charging"],
        value_fn=lambda state: _enum_from_map(
            state.get("chargingState"),
            {
                0: "not_charging",
                1: "charging",
            },
        ),
    ),
    NinebotSensorDescription(
        key="pwr",
        translation_key="pwr",
        icon="mdi:power",
        device_class=SensorDeviceClass.ENUM,
        options=["off", "on"],
        value_fn=lambda state: _enum_from_map(
            state.get("pwr"),
            {
                0: "off",
                1: "on",
            },
        ),
    ),
    NinebotSensorDescription(
        key="power_status",
        translation_key="power_status",
        icon="mdi:scooter",
        value_fn=lambda state: state.get("powerStatus"),
    ),
    NinebotSensorDescription(
        key="location_desc",
        translation_key="location_desc",
        icon="mdi:map-marker",
        value_fn=lambda state: (
            (state.get("locationInfo") or {}).get("locationDesc")
            if isinstance(state.get("locationInfo"), dict)
            else None
        ),
    ),
    NinebotSensorDescription(
        key="remain_charge_time",
        translation_key="remain_charge_time",
        icon="mdi:timer-sand",
        value_fn=lambda state: state.get("remainChargeTime"),
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

    @property
    def name(self) -> str | None:
        return self.entity_description.name

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._state)
