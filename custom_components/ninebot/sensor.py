"""Sensors with explicit physical meaning; unresolved energy units stay raw."""

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ESTIMATION
from .entity import NinebotEntity, async_setup_dynamic, legacy_rows
from .models import VehicleSnapshot
from .runtime import NinebotConfigEntry


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    group: str
    value: Callable[[VehicleSnapshot], str | float | None]
    aliases: tuple[str, ...] = ()


SENSORS = (
    Description(
        key="battery",
        group="status",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.status.battery,
    ),
    Description(
        key="endurance",
        group="status",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        aliases=("remaining_range",),
        value=lambda s: s.status.range_precise,
    ),
    Description(
        key="range_estimated",
        group="status",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=lambda s: s.status.range_estimated,
    ),
    Description(
        key="range_ai",
        group="status",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=lambda s: s.status.range_ai,
    ),
    Description(
        key="remaining_charge_time",
        group="status",
        value=lambda s: s.status.charge_remaining,
        entity_registry_enabled_default=False,
    ),
    Description(
        key="device_name",
        group="profile",
        value=lambda s: s.profile.name,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    Description(
        key="sn",
        group="profile",
        value=lambda s: s.profile.sn,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    Description(
        key="vehicle_lock_raw",
        group="status",
        value=lambda s: int(not s.status.locked) if s.status.locked is not None else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    Description(
        key="month_mileage",
        group="travel",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value=lambda s: s.travel.mileage if s.travel else None,
    ),
    Description(
        key="last_mileage",
        group="travel",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_registry_enabled_default=False,
        value=lambda s: s.travel.last_ride.mileage if s.travel and s.travel.last_ride else None,
    ),
    Description(
        key="month_energy_raw",
        group="travel",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.travel.energy_raw if s.travel else None,
    ),
    Description(
        key="last_energy_raw",
        group="travel",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.travel.last_ride.energy_raw if s.travel and s.travel.last_ride else None,
    ),
    Description(
        key="charging_power_raw",
        group="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.battery.charging_power_raw,
    ),
)

# Old estimated energy identities cannot become v2 SOC-model identities.
LEGACY_KEYS = {
    "battery_calculated",
    "gsm_csq",
    "gsm_rssi",
    "gsm_report_timestamp",
    "gsm_report_time",
    "location",
    "battery_nominal_energy",
    "battery_energy_delta",
    "battery_outflow_energy_step",
    "battery_inflow_energy_step",
    "battery_outflow_power",
    "battery_inflow_power",
    "battery_outflow_energy_daily",
    "battery_outflow_energy_monthly",
    "battery_outflow_energy_total",
    "battery_inflow_energy_daily",
    "battery_inflow_energy_monthly",
    "battery_inflow_energy_total",
    "month_energy",
    "last_energy",
}


class NinebotSensor(NinebotEntity, SensorEntity):
    entity_description: Description

    def __init__(
        self,
        entry: NinebotConfigEntry,
        sn: str,
        description: Description,
        *,
        unique_id: str | None = None,
    ) -> None:
        super().__init__(
            entry,
            sn,
            description.key,
            "sensor",
            description.group,
            description.aliases,
            unique_id=unique_id,
        )
        self.entity_description = description
        self._attr_translation_key = description.translation_key or description.key

    @property
    def native_value(self) -> str | float | None:
        return self.entity_description.value(self.snapshot) if self.snapshot else None


class LegacySensor(NinebotEntity, SensorEntity):
    """Keep registry/history; missing sources are never represented as zero."""

    _attr_name = "Legacy value (unavailable)"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str, unique_id: str) -> None:
        super().__init__(entry, sn, key, "sensor", "profile", unique_id=unique_id)
        self._attr_translation_key = "legacy"

    @property
    def native_value(self) -> None:
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"status": "deprecated", "reason": "no_equivalent_source_or_changed_model"}


class EstimatedSensor(NinebotEntity, SensorEntity):
    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str, generation: int) -> None:
        super().__init__(entry, sn, f"estimated_{key}_v2_g{generation}", "sensor", "status")
        self._attr_translation_key = f"estimated_{key}"
        self.key = key
        self.generation = generation
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        if key.endswith(("_daily", "_monthly", "_total")):
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        model = self.entry.runtime_data.models.model(self.sn)
        return (
            super().available
            and model.nominal is not None
            and model.generation == self.generation
            and bool(self.entry.options.get(CONF_ESTIMATION))
        )

    @property
    def native_value(self) -> float | None:
        model = self.entry.runtime_data.models.model(self.sn)
        if self.key == "nominal":
            return model.nominal
        return model.values.get(self.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        model = self.entry.runtime_data.models.model(self.sn)
        return {"model_version": 2, "generation": self.generation, "quality": model.quality}


def battery_descriptions(snapshot: VehicleSnapshot) -> list[Description]:
    result = []
    for battery in snapshot.battery.batteries:
        if len(snapshot.battery.batteries) == 1:
            prefix = ""
        elif battery.identified:
            prefix = f"battery_{hashlib.sha256(battery.key.encode()).hexdigest()[:12]}_"
        else:
            continue  # Slot order cannot identify multiple interchangeable packs.
        for key, field, unit, device_class in [
            ("bms_voltage", "voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
            ("batt_temp", "temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
            ("bms_cycles", "cycles", None, None),
        ]:
            if key == "bms_cycles" and battery.cycle_supported is not True:
                continue
            identity = battery.key

            def value(
                s: VehicleSnapshot,
                field: str = field,
                identity: str = identity,
                primary: bool = not prefix,
            ) -> float | None:
                found = next((b for b in s.battery.batteries if b.key == identity), None)
                if found is None and len(s.battery.batteries) == 1 and primary:
                    found = s.battery.batteries[0]
                return getattr(found, field) if found else None

            result.append(
                Description(
                    key=f"{prefix}{key}",
                    translation_key=key,
                    group="battery",
                    value=value,
                    native_unit_of_measurement=unit,
                    device_class=device_class,
                    state_class=SensorStateClass.MEASUREMENT if device_class else None,
                    entity_category=EntityCategory.DIAGNOSTIC if key == "bms_cycles" else None,
                    entity_registry_enabled_default=key != "bms_cycles",
                )
            )
    return result


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    def factory(sn: str) -> Iterable[Entity]:
        snapshot = entry.runtime_data.coordinator.data[sn]
        yield from (
            NinebotSensor(entry, sn, d) for d in (*SENSORS, *battery_descriptions(snapshot))
        )
        if entry.options.get(CONF_ESTIMATION):
            generation = entry.runtime_data.models.model(sn).generation
            for key in (
                "nominal",
                "delta",
                "out_step",
                "in_step",
                "out_daily",
                "out_monthly",
                "out_total",
                "in_daily",
                "in_monthly",
                "in_total",
            ):
                yield EstimatedSensor(entry, sn, key, generation)

    seen = async_setup_dynamic(hass, entry, add, factory)
    equivalent = {
        key: description
        for description in SENSORS
        for key in (description.key, *description.aliases)
    }
    known = LEGACY_KEYS | set(equivalent) | {"bms_voltage", "batt_temp", "bms_cycles"}
    existing: list[Entity] = []
    for sn, key, uid in legacy_rows(hass, entry, "sensor", known):
        if uid in seen:
            continue
        if key in equivalent:
            existing.append(NinebotSensor(entry, sn, equivalent[key], unique_id=uid))
        else:
            existing.append(LegacySensor(entry, sn, key, uid))
    if existing:
        add(existing)
