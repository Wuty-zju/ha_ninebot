"""A per-vehicle refresh and explicitly gated vehicle controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capabilities import CONTROL_BUTTONS
from .compat import device_by_identifier
from .const import DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry

# The account broker owns wire admission; command observers remain replaceable.
PARALLEL_UPDATES = 0


class NinebotButton(NinebotEntity, ButtonEntity):
    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str, action: str | None) -> None:
        super().__init__(entry, sn, key, "button", "profile", ("info",) if key == "refresh" else ())
        self.action = action
        if action is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return super().available and (
            self.action is None or self.coordinator.controls_enabled(self.sn, self.action)
        )

    async def async_press(self) -> None:
        if self.action is None:
            await self.coordinator.async_refresh_vehicle(self.sn)
        else:
            await self.coordinator.async_control(self.sn, self.action)


class HistorySyncButton(NinebotEntity, ButtonEntity):
    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str, operation: str) -> None:
        super().__init__(entry, sn, key, "button", "profile")
        self.operation = operation
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.statistics.async_add_archive_listener(self.sn, self._archive_updated)
        )

    @callback
    def _archive_updated(self) -> None:
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        progress = self.coordinator.preservation.progress.get(self.sn)
        return {"sync_state": progress.state if progress else "not_started"}

    @property
    def available(self) -> bool:
        return (
            self.coordinator.local_vehicle_available(self.sn)
            and self.coordinator.statistics.archive_ready
            and (
                self.operation != "synchronize"
                or self.entry.options.get("preserve_history") is True
            )
        )

    @property
    def icon(self) -> str:
        progress = self.coordinator.preservation.progress.get(self.sn)
        state = progress.state if progress else "not_started"
        if state == "paused":
            return "mdi:pause-circle-outline"
        if state in {"waiting_budget", "waiting_retry"}:
            return "mdi:timer-sand"
        if self.operation == "pause":
            return "mdi:pause"
        return (
            "mdi:sync"
            if state in {"discovering", "preserving", "incremental", "verifying"}
            else "mdi:cloud-sync-outline"
        )

    async def async_press(self) -> None:
        identifiers = self.device_info.get("identifiers") or set()
        identifier = next(value for domain, value in identifiers if domain == DOMAIN)
        device = device_by_identifier(dr.async_get(self.hass), self.entry.entry_id, identifier)
        device_id = getattr(device, "id", None)
        if not isinstance(device_id, str):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="statistics_unavailable"
            )
        await self.hass.services.async_call(
            DOMAIN,
            "sync_history",
            {"device_id": device_id, "operation": self.operation},
            blocking=True,
            return_response=True,
            context=self._context,
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        add,
        lambda sn: (
            [
                NinebotButton(entry, sn, key, action)
                for key, action in (("refresh", None), *CONTROL_BUTTONS)
            ]
            + [
                HistorySyncButton(entry, sn, key, operation)
                for key, operation in (
                    ("history_sync", "synchronize"),
                    ("history_pause", "pause"),
                )
            ]
        ),
    )
