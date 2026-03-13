"""Binary sensor platform for Ninebot integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHARGING_OFF,
    CHARGING_ON,
    DATA_COORDINATOR,
    DOMAIN,
    MAIN_POWER_OFF,
    MAIN_POWER_ON,
    lock_status_from_state,
)
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


def _vehicle_lock_binary_value(state: dict[str, Any]) -> bool | None:
    raw = lock_status_from_state(state)
    if raw == 0:
        return True
    if raw == 1:
        return False
    return None


@dataclass(frozen=True, kw_only=True)
class NinebotBinaryDescription(BinarySensorEntityDescription):
    """Describe Ninebot binary sensor behavior."""

    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_DESCRIPTIONS: tuple[NinebotBinaryDescription, ...] = (
    NinebotBinaryDescription(
        key="charging",
        translation_key="charging",
        icon="mdi:battery-charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda state: True if state.get("chargingState") == CHARGING_ON else False if state.get("chargingState") == CHARGING_OFF else None,
    ),
    NinebotBinaryDescription(
        key="main_power",
        translation_key="main_power",
        icon="mdi:power-plug",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda state: True if state.get("pwr") == MAIN_POWER_ON else False if state.get("pwr") == MAIN_POWER_OFF else None,
    ),
    NinebotBinaryDescription(
        key="vehicle_lock",
        translation_key="vehicle_lock",
        icon="mdi:lock",
        device_class=BinarySensorDeviceClass.LOCK,
        # Keep semantics aligned with lock entity:
        # 0 -> locked -> on, 1 -> unlocked -> off.
        value_fn=_vehicle_lock_binary_value,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ninebot binary sensors."""
    coordinator: NinebotDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[NinebotBinarySensor] = []
    for sn in coordinator.data:
        for description in BINARY_DESCRIPTIONS:
            entities.append(NinebotBinarySensor(coordinator, sn, description))
    async_add_entities(entities)


class NinebotBinarySensor(NinebotCoordinatorEntity, BinarySensorEntity):
    """Ninebot converted binary status entity."""

    entity_description: NinebotBinaryDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NinebotDataUpdateCoordinator,
        sn: str,
        description: NinebotBinaryDescription,
    ) -> None:
        super().__init__(coordinator, sn)
        self.entity_description = description
        self._attr_unique_id = self._build_unique_id(description.key)
        self._attr_suggested_object_id = self._build_object_id(description.key)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._state)

    @property
    def icon(self) -> str | None:
        key = self.entity_description.key
        value = self.is_on
        if key == "vehicle_lock":
            if value is None:
                return "mdi:lock"
            return "mdi:lock" if value else "mdi:lock-open-variant"
        if key == "charging":
            if value is None:
                return "mdi:battery"
            return "mdi:battery-charging" if value else "mdi:battery"
        if key == "main_power":
            if value is None:
                return "mdi:power-plug"
            return "mdi:power-plug" if value else "mdi:power-plug-off"
        return self.entity_description.icon
