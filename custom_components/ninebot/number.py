"""Local model parameters, never populated from measured BMS voltage."""

from collections.abc import Iterable

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ESTIMATION
from .entity import NinebotEntity, async_setup_dynamic
from .runtime import NinebotConfigEntry


class ModelNumber(NinebotEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = 1
    _attr_native_step = 1

    def __init__(
        self, entry: NinebotConfigEntry, sn: str, key: str, field: str, unit: str, maximum: int
    ) -> None:
        super().__init__(entry, sn, key, "number", "profile")
        self.field = field
        self._attr_native_unit_of_measurement = unit
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> float | None:
        return getattr(self.entry.runtime_data.models.model(self.sn), self.field)

    async def async_set_native_value(self, value: float) -> None:
        self.entry.runtime_data.models.model(self.sn).configure(self.field, value)
        self.entry.runtime_data.models.schedule_save()
        self.coordinator.async_set_updated_data(dict(self.coordinator.data))


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    def factory(sn: str) -> Iterable[Entity]:
        # Keep legacy parameters discoverable; new users opt into the model.
        if entry.options.get(CONF_ESTIMATION) or entry.data.get("identity_scheme") == "open_v1":
            for key, field, unit, maximum in [
                ("main_battery_voltage", "voltage", "V", 300),
                ("battery_capacity", "capacity", "Ah", 500),
                ("battery_max_range", "max_range", "km", 5000),
            ]:
                yield ModelNumber(entry, sn, key, field, unit, maximum)

    async_setup_dynamic(hass, entry, add, factory)
