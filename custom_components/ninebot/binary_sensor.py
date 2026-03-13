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

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class NinebotBinaryDescription(BinarySensorEntityDescription):
    """Describe Ninebot binary sensor behavior."""

    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_DESCRIPTIONS: tuple[NinebotBinaryDescription, ...] = (
    NinebotBinaryDescription(
        key="is_charging",
        translation_key="is_charging",
        icon="mdi:battery-charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda state: True if state.get("chargingState") == 1 else False if state.get("chargingState") == 0 else None,
    ),
    NinebotBinaryDescription(
        key="main_power_connected",
        translation_key="main_power_connected",
        icon="mdi:power-plug",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda state: True if state.get("pwr") == 1 else False if state.get("pwr") == 0 else None,
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
        self._attr_unique_id = f"{sn}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._state)
