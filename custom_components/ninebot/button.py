"""A per-vehicle refresh and explicitly gated vehicle controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capabilities import CONTROL_BUTTONS
from .const import DOMAIN
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


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
        self._attr_entity_category = EntityCategory.CONFIG

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

    async def async_press(self) -> None:
        device = dr.async_get(self.hass).async_get_device(
            identifiers=self.device_info.get("identifiers")
        )
        if device is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="statistics_unavailable"
            )
        await self.hass.services.async_call(
            DOMAIN,
            "sync_history",
            {"device_id": device.id, "operation": self.operation},
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
                    ("history_resume", "resume"),
                )
            ]
        ),
    )
