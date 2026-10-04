"""Current measurements and explicitly labelled, bounded scalar observations."""

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
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
    UnitOfPower,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .battery import current_battery, identified_battery
from .capabilities import CONTROL_BUTTONS, CONTROL_STATES, control_state
from .const import CONF_ESTIMATION
from .entity import NinebotEntity, async_setup_dynamic, legacy_rows
from .models import VehicleSnapshot
from .observations import RAW_FIELDS, RawField
from .parsing import display_scalar
from .ride_models import Ride
from .runtime import NinebotConfigEntry


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    group: str
    value: Callable[[VehicleSnapshot], str | float | datetime | None]
    aliases: tuple[str, ...] = ()
    attributes: Callable[[VehicleSnapshot], dict[str, Any]] | None = None


def raw_attributes(snapshot: VehicleSnapshot, field: RawField) -> dict[str, Any]:
    values = getattr(snapshot, field.group).observations
    value = values.get(field.path)
    return {
        "source": f"{field.group}.{field.path}",
        "raw_type": type(value).__name__,
        "interpretation": "unverified" if value is not None else "not_reported",
    }


def remaining_charge_attributes(snapshot: VehicleSnapshot) -> dict[str, Any]:
    value = snapshot.status.observations.get("remain_charge_time")
    return {
        "source": "status.remain_charge_time",
        "raw": value,
        "interpretation": "unparsed" if value else "not_reported",
        "battery_source_raw": snapshot.battery.observations.get("remain_charge_time"),
    }


def raw_description(field: RawField) -> Description:
    def value(snapshot: VehicleSnapshot) -> str | float | None:
        return display_scalar(getattr(snapshot, field.group).observations.get(field.path))

    def attributes(snapshot: VehicleSnapshot) -> dict[str, Any]:
        return raw_attributes(snapshot, field)

    return Description(
        key=field.key,
        group=field.group,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=value,
        attributes=attributes,
    )


def last_timed_ride(snapshot: VehicleSnapshot) -> Ride | None:
    """New ride measurements require a known latest completed-time observation."""
    last = snapshot.travel.last_ride if snapshot.travel else None
    ride = last.ride if last else None
    if (
        ride is None
        or ride.ended_at is None
        or ride.ended_at > dt_util.utcnow()
        or any(
            issue in ride.issues
            for issue in ("reversed_timestamps", "conflicting_time_representations")
        )
    ):
        return None
    return ride


def ride_value(snapshot: VehicleSnapshot, field: str) -> float | datetime | None:
    ride = last_timed_ride(snapshot)
    return getattr(ride, field) if ride else None


def ride_speed(snapshot: VehicleSnapshot, field: str) -> float | None:
    ride = last_timed_ride(snapshot)
    if ride and field == "average_speed_m_s" and "duration_time_difference" in ride.issues:
        return None
    value = getattr(ride, field) if ride else None
    return value * 3.6 if value is not None else None


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
        value=lambda s: s.status.range_estimated,
    ),
    Description(
        key="range_ai",
        group="status",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.status.range_ai,
    ),
    Description(
        key="remaining_charge_time",
        group="status",
        value=lambda s: s.status.charge_remaining,
        attributes=remaining_charge_attributes,
    ),
    Description(
        key="device_name",
        group="profile",
        value=lambda s: s.profile.name,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    Description(
        key="sn",
        group="profile",
        value=lambda s: s.profile.sn,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    Description(
        key="vehicle_lock_raw",
        group="status",
        value=lambda s: int(not s.status.locked) if s.status.locked is not None else None,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        value=lambda s: s.travel.last_ride.mileage if s.travel and s.travel.last_ride else None,
    ),
    Description(
        key="month_energy_raw",
        group="travel",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value=lambda s: s.travel.energy_raw if s.travel else None,
    ),
    Description(
        key="last_energy_raw",
        group="travel",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value=lambda s: s.travel.last_ride.energy_raw if s.travel and s.travel.last_ride else None,
    ),
    Description(
        key="charging_power_raw",
        group="battery",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.battery.charging_power_raw,
    ),
    Description(
        key="month_ride_count",
        group="travel",
        value=lambda s: s.travel.reported_ride_count if s.travel else None,
    ),
    Description(
        key="month_duration",
        group="travel",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value=lambda s: s.travel.reported_duration_s if s.travel else None,
    ),
    Description(
        key="month_returned_rides",
        group="travel",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.travel.summary.returned_count if s.travel and s.travel.summary else None,
    ),
    Description(
        key="month_list_coverage",
        group="travel",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: (
            100 * s.travel.summary.coverage
            if s.travel and s.travel.summary and s.travel.summary.coverage is not None
            else None
        ),
        attributes=lambda s: {
            "scope": "returned_month_list",
            "list_complete": s.travel.summary.list_complete
            if s.travel and s.travel.summary
            else None,
        },
    ),
    Description(
        key="returned_pack_count",
        group="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: len(s.battery.batteries),
    ),
    Description(
        key="last_battery_used_raw",
        group="travel",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: ride_value(s, "used_electricity_raw"),
    ),
    Description(
        key="last_ride_duration",
        group="travel",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value=lambda s: ride_value(s, "duration_s"),
    ),
    Description(
        key="last_ride_start",
        group="travel",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s: ride_value(s, "started_at"),
    ),
    Description(
        key="last_ride_end",
        group="travel",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s: ride_value(s, "ended_at"),
    ),
    Description(
        key="last_ride_max_speed",
        group="travel",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value=lambda s: ride_speed(s, "server_max_speed_m_s"),
    ),
    Description(
        key="last_ride_average_speed",
        group="travel",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value=lambda s: ride_speed(s, "average_speed_m_s"),
    ),
    *(raw_description(field) for field in RAW_FIELDS),
)


class ControlAvailabilitySensor(NinebotEntity, SensorEntity):
    """Explain local gating, without claiming cloud permission or device state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(CONTROL_STATES)
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "control_availability", "sensor", "profile")

    @property
    def available(self) -> bool:
        # Local policy remains useful when cloud measurements/auth are stale.
        return bool(self.snapshot and self.snapshot.present and not self.coordinator._stopping)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            key: control_state(self.coordinator.control_decision(self.sn, action))
            for key, action in CONTROL_BUTTONS
        }

    @property
    def native_value(self) -> str:
        states = self.extra_state_attributes.values()
        return next(state for state in CONTROL_STATES if state in states)


class RawDataSummarySensor(NinebotEntity, SensorEntity):
    """Optional debug overview, without exposing raw data to state/recorder."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "raw_data_summary", "sensor", "profile")

    @property
    def available(self) -> bool:
        return bool(self.snapshot and self.snapshot.present and not self.coordinator._stopping)

    @property
    def native_value(self) -> int:
        return self.coordinator.raw.vehicle_summary(self.sn, dt_util.utcnow())["record_count"]

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        summary = self.coordinator.raw.vehicle_summary(self.sn, dt_util.utcnow())
        summary.pop("record_count")
        return summary


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
    def native_value(self) -> str | float | datetime | None:
        return self.entity_description.value(self.snapshot) if self.snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.snapshot and self.entity_description.attributes:
            return self.entity_description.attributes(self.snapshot)
        return None


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
            ("cycle_raw", "cycle_raw", None, None),
            ("health_score", "score_raw", None, None),
            ("pack_electricity_raw", "electricity_raw", None, None),
        ]:
            if key == "bms_cycles" and battery.cycle_supported is not True:
                continue
            identity = battery.key

            def value(
                s: VehicleSnapshot,
                field: str = field,
                identity: str = identity,
                primary: bool = not prefix,
            ) -> str | float | None:
                found = (
                    current_battery(s.battery)
                    if primary
                    else identified_battery(s.battery, identity)
                )
                if found and field == "cycles" and found.cycle_supported is not True:
                    return None
                return display_scalar(getattr(found, field)) if found else None

            def attributes(
                s: VehicleSnapshot,
                field: str = field,
                identity: str = identity,
                primary: bool = not prefix,
            ) -> dict[str, Any]:
                found = (
                    current_battery(s.battery)
                    if primary
                    else identified_battery(s.battery, identity)
                )
                return {
                    "interpretation": "unverified",
                    "cycle_supported": found.cycle_supported if found else None,
                }

            result.append(
                Description(
                    key=f"{prefix}{key}",
                    translation_key=key,
                    group="battery",
                    value=value,
                    native_unit_of_measurement=unit,
                    device_class=device_class,
                    state_class=SensorStateClass.MEASUREMENT if device_class else None,
                    entity_category=EntityCategory.DIAGNOSTIC if device_class is None else None,
                    attributes=attributes if field.endswith("_raw") else None,
                )
            )
    return result


def legacy_battery_description(key: str) -> Description:
    """An old primary-pack ID is meaningful only while there is one pack."""
    field, unit, device_class = {
        "bms_voltage": ("voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        "batt_temp": ("temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        "bms_cycles": ("cycles", None, None),
    }[key]

    def value(snapshot: VehicleSnapshot) -> float | None:
        battery = current_battery(snapshot.battery)
        if battery is None:
            return None
        return (
            getattr(battery, field)
            if key != "bms_cycles" or battery.cycle_supported is True
            else None
        )

    return Description(
        key=key,
        group="battery",
        value=value,
        native_unit_of_measurement=unit,
        device_class=device_class,
        state_class=SensorStateClass.MEASUREMENT if device_class else None,
        entity_category=EntityCategory.DIAGNOSTIC if key == "bms_cycles" else None,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: NinebotConfigEntry, add: AddEntitiesCallback
) -> None:
    def factory(sn: str) -> Iterable[Entity]:
        snapshot = entry.runtime_data.coordinator.data[sn]
        yield from (
            NinebotSensor(entry, sn, d) for d in (*SENSORS, *battery_descriptions(snapshot))
        )
        yield ControlAvailabilitySensor(entry, sn)
        yield RawDataSummarySensor(entry, sn)
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
    known = set(equivalent) | {"bms_voltage", "batt_temp", "bms_cycles"}
    existing: list[Entity] = []
    for sn, key, uid in legacy_rows(hass, entry, "sensor", known):
        if uid in seen:
            continue
        seen.add(uid)
        if key in equivalent:
            existing.append(NinebotSensor(entry, sn, equivalent[key], unique_id=uid))
        elif key in {"bms_voltage", "batt_temp", "bms_cycles"}:
            existing.append(
                NinebotSensor(entry, sn, legacy_battery_description(key), unique_id=uid)
            )
    if existing:
        add(existing)
