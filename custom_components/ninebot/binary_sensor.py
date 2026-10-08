"""Strict binary states; HA lock binary sensors are on when UNLOCKED."""

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import NinebotEntity, async_setup_dynamic
from .parsing import boolean
from .runtime import NinebotConfigEntry


@dataclass(frozen=True, kw_only=True)
class Description(BinarySensorEntityDescription):
    field: str
    aliases: tuple[str, ...] = ()
    group: str = "status"


DESCRIPTIONS = (
    Description(
        key="charging", field="charging", device_class=BinarySensorDeviceClass.BATTERY_CHARGING
    ),
    Description(
        key="power",
        field="powered",
        aliases=("main_power",),
        device_class=BinarySensorDeviceClass.POWER,
    ),
    Description(
        key="cycle_support",
        field="have_bms_cycle_support",
        group="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    Description(
        key="battery_find_my_support",
        field="battery_find_my_support",
        group="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class NinebotBinarySensor(NinebotEntity, BinarySensorEntity):
    entity_description: Description

    def __init__(self, entry: NinebotConfigEntry, sn: str, description: Description) -> None:
        super().__init__(
            entry, sn, description.key, "binary_sensor", description.group, description.aliases
        )
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if self.snapshot and self.entity_description.group == "battery":
            return boolean(self.snapshot.battery.observations.get(self.entity_description.field))
        value = (
            getattr(self.snapshot.status, self.entity_description.field) if self.snapshot else None
        )
        if value is None:
            return None
        return not value if self.entity_description.key == "unlocked" else value


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass, entry, add, lambda sn: (NinebotBinarySensor(entry, sn, d) for d in DESCRIPTIONS)
    )
