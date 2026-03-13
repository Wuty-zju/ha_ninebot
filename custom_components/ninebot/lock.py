"""Lock platform for Ninebot integration."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ninebot lock entities."""
    coordinator: NinebotDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities = [NinebotVehicleLock(coordinator, sn) for sn in coordinator.data]
    async_add_entities(entities)


class NinebotVehicleLock(NinebotCoordinatorEntity, LockEntity):
    """Converted lock state from powerStatus."""

    _attr_translation_key = "vehicle_lock"
    _attr_icon = "mdi:lock"
    _attr_has_entity_name = True

    def __init__(self, coordinator: NinebotDataUpdateCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_vehicle_lock"

    @property
    def is_locked(self) -> bool | None:
        value = self._state.get("powerStatus")
        if not isinstance(value, int):
            return None
        return value == 0

    async def async_lock(self, **kwargs) -> None:
        raise NotImplementedError("Ninebot lock control is not implemented yet")

    async def async_unlock(self, **kwargs) -> None:
        raise NotImplementedError("Ninebot unlock control is not implemented yet")
