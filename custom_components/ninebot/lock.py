"""Observed lock state and opt-in experimental engine controls."""

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotLock(NinebotEntity, LockEntity):
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "lock", "lock", "status", ("vehicle_lock_control",))

    @property
    def is_locked(self) -> bool | None:
        return self.snapshot.status.locked if self.snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        return {"experimental_controls_enabled": False}

    async def async_lock(self, **kwargs: Any) -> None:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="engine_lock_unverified"
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="engine_lock_unverified"
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, add, lambda sn: [NinebotLock(entry, sn)])
