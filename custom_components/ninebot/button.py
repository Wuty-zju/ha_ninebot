"""Button platform for Ninebot integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import NinebotDataUpdateCoordinator
from .entity import NinebotCoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class NinebotButtonDescription(ButtonEntityDescription):
    """Description for Ninebot button entities."""


BUTTON_DESCRIPTIONS: tuple[NinebotButtonDescription, ...] = (
    NinebotButtonDescription(
        key="polling_raw_json_info",
        translation_key="polling_raw_json_info",
        icon="mdi:code-json",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ninebot debug buttons."""
    coordinator: NinebotDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    if not coordinator.debug_enabled:
        return

    entities: list[NinebotDebugButton] = []
    for sn in coordinator.data:
        for description in BUTTON_DESCRIPTIONS:
            entities.append(NinebotDebugButton(coordinator, sn, description))

    async_add_entities(entities)


class NinebotDebugButton(NinebotCoordinatorEntity, ButtonEntity):
    """Show raw polling payload in attributes and trigger manual refresh on press."""

    entity_description: NinebotButtonDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NinebotDataUpdateCoordinator,
        sn: str,
        description: NinebotButtonDescription,
    ) -> None:
        super().__init__(coordinator, sn)
        self.entity_description = description
        self._attr_unique_id = self._build_unique_id(description.key)
        self._attr_suggested_object_id = self._build_object_id(description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        payload = self.coordinator.get_raw_polling_payload(self._sn)
        if payload is None:
            return {
                "debug_enabled": self.coordinator.debug_enabled,
                "message": "No raw payload yet, wait for next polling cycle.",
            }
        return {
            "debug_enabled": self.coordinator.debug_enabled,
            "raw_polling_payload": payload,
        }

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
