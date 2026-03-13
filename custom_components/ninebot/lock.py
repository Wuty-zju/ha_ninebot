"""Lock platform for Ninebot integration."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN, STATUS_LOCKED
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
    """Read-only lock entity converted from status."""

    _attr_translation_key = "vehicle_lock_control"
    _attr_icon = "mdi:lock"
    _attr_has_entity_name = True
    _attr_supported_features = 0

    def __init__(self, coordinator: NinebotDataUpdateCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = self._build_unique_id("lock", "vehicle_lock_control")
        self._attr_suggested_object_id = self._build_object_id("vehicle_lock_control")

    @property
    def is_locked(self) -> bool | None:
        value = self._state.get("status")
        if not isinstance(value, int):
            return None
        return value == STATUS_LOCKED

    @property
    def icon(self) -> str | None:
        locked = self.is_locked
        if locked is None:
            return "mdi:lock"
        return "mdi:lock" if locked else "mdi:lock-open-variant"

    async def async_lock(self, **kwargs) -> None:
        raise NotImplementedError("Ninebot lock is read-only and cannot be controlled")

    async def async_unlock(self, **kwargs) -> None:
        raise NotImplementedError("Ninebot lock is read-only and cannot be controlled")
