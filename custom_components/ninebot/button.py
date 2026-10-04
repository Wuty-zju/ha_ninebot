"""A per-vehicle refresh and explicitly gated vehicle controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capabilities import CONTROL_BUTTONS
from .const import CONF_CONTROL_VEHICLES, CONF_CONTROLS
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class NinebotButton(NinebotEntity, ButtonEntity):
    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str, action: str | None) -> None:
        super().__init__(entry, sn, key, "button", "profile", ("info",) if key == "refresh" else ())
        self.action = action
        self._attr_entity_registry_enabled_default = action is None or (
            entry.options.get(CONF_CONTROLS) is True
            and sn in entry.options.get(CONF_CONTROL_VEHICLES, [])
        )
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


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        add,
        lambda sn: [
            NinebotButton(entry, sn, key, action)
            for key, action in (("refresh", None), *CONTROL_BUTTONS)
        ],
    )
