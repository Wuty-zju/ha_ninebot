"""Cloud-observed locks; requests never optimistically change the locked state."""

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .compat import unrecorded_attributes
from .const import DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotLock(NinebotEntity, LockEntity):
    _unrecorded_attributes = unrecorded_attributes(frozenset({"last_operation"}))

    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str) -> None:
        identities = entry.runtime_data.identities
        uid, blocked = identities.lock_identity(sn, key) if identities else (None, False)
        super().__init__(entry, sn, key, "lock", "status", unique_id=uid)
        self.key = key
        self._identity_blocked |= blocked

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        actions = ("buck",) if self.key == "seat_lock" else ("engine/start", "engine/stop")
        outcomes = self.coordinator.control_results.diagnostics(self.sn)
        latest = max(
            (outcomes[action] for action in actions if action in outcomes),
            key=lambda result: str(result["attempted_at"]),
            default=None,
        )
        return {
            "remote_lock_mode": "manual" if self.key == "seat_lock" else "remote",
            "last_operation": latest,
        }

    @property
    def is_locked(self) -> bool | None:
        return self.coordinator.lock_state(self.sn, self.key)

    @property
    def is_locking(self) -> bool:
        return (
            self.key == "vehicle_lock"
            and self.coordinator.pending_controls.get(self.sn) == "engine/stop"
        )

    @property
    def is_unlocking(self) -> bool:
        action = "buck" if self.key == "seat_lock" else "engine/start"
        return self.coordinator.pending_controls.get(self.sn) == action

    async def async_lock(self, **kwargs: object) -> None:
        if self.key == "seat_lock":
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="seat_lock_manual")
        await self.coordinator.async_control(self.sn, "engine/stop")

    async def async_unlock(self, **kwargs: object) -> None:
        await self.coordinator.async_control(
            self.sn, "buck" if self.key == "seat_lock" else "engine/start"
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        add,
        lambda sn: (NinebotLock(entry, sn, key) for key in ("vehicle_lock", "seat_lock")),
    )
