"""Cloud-observed locks with a bounded seat-unlock presentation window."""

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .compat import unrecorded_attributes
from .const import DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotLock(NinebotEntity, LockEntity):
    _unrecorded_attributes = unrecorded_attributes(
        frozenset(
            {
                "last_operation",
                "unlock_feedback",
                "reported_state",
                "pending_action",
                "command_status",
            }
        )
    )

    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str) -> None:
        identities = entry.runtime_data.identities
        uid, blocked = identities.lock_identity(sn, key) if identities else (None, False)
        super().__init__(entry, sn, key, "lock", "status", unique_id=uid)
        self.key = key
        self._identity_blocked |= blocked

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        actions = ("buck",) if self.key == "seat_lock" else ("engine/start", "engine/stop")
        latest = self.coordinator.control_results.latest(self.sn, actions)
        return {
            "remote_lock_mode": "manual" if self.key == "seat_lock" else "remote",
            "last_operation": latest,
            "command_status": latest.get("confirmation", "idle") if latest else "idle",
            "pending_action": self.coordinator.pending_lock_action(self.sn, self.key),
            **(
                {
                    "unlock_feedback": "recently_unlocked"
                    if self.sn in self.coordinator.seat_unlock_feedback
                    else "none",
                    "reported_state": {
                        True: "locked",
                        False: "unlocked",
                        None: "unknown",
                    }[self.coordinator.lock_state(self.sn, self.key)],
                }
                if self.key == "seat_lock"
                else {}
            ),
        }

    @property
    def is_locked(self) -> bool | None:
        return self.coordinator.displayed_lock_state(self.sn, self.key)

    @property
    def is_locking(self) -> bool:
        return (
            self.key == "vehicle_lock"
            and self.coordinator.pending_lock_action(self.sn, self.key) == "engine/stop"
            and self.is_locked is not True
        )

    @property
    def is_unlocking(self) -> bool:
        action = "buck" if self.key == "seat_lock" else "engine/start"
        return (
            self.coordinator.pending_lock_action(self.sn, self.key) == action
            and self.is_locked is not False
        )

    async def async_lock(self, **kwargs: object) -> None:
        if self.key == "seat_lock":
            if self.coordinator.cancel_seat_unlock_wait(self.sn):
                return
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
