"""Number platform for Ninebot integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class NinebotNumberDescription(NumberEntityDescription):
    """Description for Ninebot number entities."""


NUMBER_DESCRIPTIONS: tuple[NinebotNumberDescription, ...] = (
    NinebotNumberDescription(
        key="main_battery_voltage",
        translation_key="main_battery_voltage",
        icon="mdi:sine-wave",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        native_min_value=1.0,
        native_max_value=300.0,
        native_step=0.1,
    ),
    NinebotNumberDescription(
        key="battery_capacity",
        translation_key="battery_capacity",
        icon="mdi:battery-high",
        native_unit_of_measurement="Ah",
        native_min_value=1.0,
        native_max_value=500.0,
        native_step=0.1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ninebot number entities."""
    coordinator: NinebotDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities: list[NinebotNumberEntity] = []
    for sn in coordinator.data:
        for description in NUMBER_DESCRIPTIONS:
            entities.append(NinebotNumberEntity(coordinator, sn, description))

    async_add_entities(entities)


class NinebotNumberEntity(NinebotCoordinatorEntity, NumberEntity):
    """Per-device local configurable battery parameters."""

    entity_description: NinebotNumberDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NinebotDataUpdateCoordinator,
        sn: str,
        description: NinebotNumberDescription,
    ) -> None:
        super().__init__(coordinator, sn)
        self.entity_description = description
        self._attr_unique_id = self._build_unique_id(description.key)
        self._attr_suggested_object_id = self._build_object_id(description.key)
        self._attr_mode = "box"

    @property
    def native_value(self) -> float | None:
        value = self._state.get(self.entity_description.key)
        if isinstance(value, (int, float)):
            return float(value)
        return None

    async def async_set_native_value(self, value: float) -> None:
        if self.entity_description.key == "main_battery_voltage":
            await self.coordinator.async_set_main_battery_voltage(self._sn, float(value))
            return
        if self.entity_description.key == "battery_capacity":
            await self.coordinator.async_set_battery_capacity(self._sn, float(value))
            return
        raise ValueError(f"Unsupported number key: {self.entity_description.key}")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return {"source": "local_storage"}
