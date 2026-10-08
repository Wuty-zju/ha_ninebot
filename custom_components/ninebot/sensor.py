"""Current measurements and explicitly labelled, bounded scalar observations."""

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
    UnitOfEnergyDistance,
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
from .compat import unrecorded_attributes
from .const import BUSINESS_TIMEZONE, CONF_DEBUG, CONF_ESTIMATION, DETAIL_INTERVAL
from .debug_view import DEBUG_ATTRIBUTES, DEBUG_STATES, debug_view
from .entity import NinebotEntity, async_setup_dynamic, legacy_rows
from .models import VehicleSnapshot
from .observations import ENTITY_FIELDS, RawField
from .parsing import display_scalar, integer
from .period_statistics import DayMetric, DaySummary, day_summary
from .ride_models import Ride
from .runtime import NinebotConfigEntry
from .travel_statistics import energy_statistics


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    group: str
    value: Callable[[VehicleSnapshot], str | float | datetime | None]
    aliases: tuple[str, ...] = ()
    attributes: Callable[[VehicleSnapshot], dict[str, Any]] | None = None
    precision: Callable[[VehicleSnapshot], int | None] | None = None


def source_precision(snapshot: VehicleSnapshot, key: str) -> int | None:
    if key == "battery":
        return dict(snapshot.status.precision).get("dump_energy")
    if key == "endurance":
        field = {
            "range_precise": "precise_estimate_mileage",
            "range_estimated": "estimate_mileage",
            "range_ai": "ai_estimate_mileage",
        }.get(range_source(snapshot) or "")
        return dict(snapshot.status.precision).get(field) if field else None
    if key == "charging_power_raw":
        return dict(snapshot.battery.precision).get("charging_power")
    if snapshot.travel:
        field = {
            "month_mileage": "total_mileages",
            "month_energy_raw": "ec",
            "month_duration": "duration",
        }.get(key)
        if field:
            return dict(snapshot.travel.precision).get(field)
        ride = snapshot.travel.last_ride.ride if snapshot.travel.last_ride else None
        field = {
            "last_mileage": "mileages",
            "last_energy_raw": "ec",
            "last_ride_max_speed": "speed",
            "last_ride_duration": "duration",
        }.get(key)
        if ride and field:
            return dict(ride.precision).get(field)
    return None


def today_mileage(snapshot: VehicleSnapshot, now: datetime) -> float | None:
    """Project only a validated chart sampled on the current business day."""
    local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
    travel = snapshot.travel
    succeeded = snapshot.travel_freshness.succeeded_at
    if (
        not snapshot.present
        or travel is None
        or travel.summary is None
        or travel.month != local.strftime("%Y%m")
        or travel.summary.month != travel.month
        or travel.summary.chart_status != "valid"
        or not snapshot.travel_freshness.valid(now, 3 * DETAIL_INTERVAL)
        or succeeded is None
        or succeeded.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date() != local.date()
    ):
        return None
    return next(
        (point.distance_km for point in travel.summary.daily_mileage if point.day == local.date()),
        None,
    )


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
    states = {
        "acc_raw": {0: "off", 1: "on"},
        "battery_present_raw": {0: "absent", 1: "present"},
        "service_expired_raw": {0: "active", 1: "expired"},
    }.get(field.key)

    def value(snapshot: VehicleSnapshot) -> str | float | None:
        raw = getattr(snapshot, field.group).observations.get(field.path)
        if states is not None:
            code = integer(raw, 0, 255)
            return states.get(code, "unrecognized") if code is not None else None
        return display_scalar(raw)

    def attributes(snapshot: VehicleSnapshot) -> dict[str, Any]:
        result = raw_attributes(snapshot, field)
        if states and value(snapshot) in states.values():
            result["interpretation"] = "interpreted"
        return result

    return Description(
        key=field.key,
        group=field.group,
        entity_category=None if states else EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM if states else None,
        options=[*states.values(), "unrecognized"] if states else None,
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


def remaining_range(snapshot: VehicleSnapshot) -> float | None:
    for value in (
        snapshot.status.range_precise,
        snapshot.status.range_estimated,
        snapshot.status.range_ai,
    ):
        if value is not None:
            return value
    return None


def range_source(snapshot: VehicleSnapshot) -> str | None:
    for key in ("range_precise", "range_estimated", "range_ai"):
        if getattr(snapshot.status, key) is not None:
            return key
    return None


def energy_intensity(snapshot: VehicleSnapshot, *, last: bool = False) -> float | None:
    travel = snapshot.travel
    if last:
        ride = last_timed_ride(snapshot)
        distance = ride.distance_m / 1000 if ride and ride.distance_m is not None else None
        energy = ride.energy_raw if ride else None
    else:
        distance = travel.mileage if travel else None
        energy = travel.energy_raw if travel else None
    return energy_statistics(
        distance, energy, basis="last_ride" if last else "server_month_summary"
    )["energy_intensity_wh_per_km"]


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
        value=remaining_range,
        attributes=lambda s: {"source": range_source(s)},
    ),
    Description(
        key="remaining_charge_time",
        group="status",
        value=lambda s: s.status.charge_remaining,
        attributes=remaining_charge_attributes,
    ),
    Description(
        key="today_mileage",
        group="travel",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value=lambda s: today_mileage(s, dt_util.utcnow()),
        attributes=lambda s: {
            "source": "travel.detail",
            "business_timezone": BUSINESS_TIMEZONE,
            "query_month": s.travel.month if s.travel else None,
        },
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
        key="emergency_battery",
        group="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value=lambda s: s.battery.emergency_soc,
    ),
    Description(
        key="main_battery_type",
        group="battery",
        device_class=SensorDeviceClass.ENUM,
        options=["lithium", "lead_acid", "unrecognized"],
        value=lambda s: s.battery.battery_type,
    ),
    Description(
        key="month_ride_count",
        group="travel",
        suggested_display_precision=0,
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
            "returned_rides": s.travel.summary.returned_count
            if s.travel and s.travel.summary
            else None,
            "list_complete": s.travel.summary.list_complete
            if s.travel and s.travel.summary
            else None,
        },
    ),
    Description(
        key="month_energy_intensity",
        group="travel",
        suggested_display_precision=1,
        device_class=SensorDeviceClass.ENERGY_DISTANCE,
        native_unit_of_measurement=UnitOfEnergyDistance.WATT_HOUR_PER_KM,
        state_class=SensorStateClass.MEASUREMENT,
        value=energy_intensity,
        attributes=lambda s: {"basis": "server_month_summary", "method": "Wh/km"},
    ),
    Description(
        key="last_energy_intensity",
        group="travel",
        suggested_display_precision=1,
        device_class=SensorDeviceClass.ENERGY_DISTANCE,
        native_unit_of_measurement=UnitOfEnergyDistance.WATT_HOUR_PER_KM,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: energy_intensity(s, last=True),
        attributes=lambda s: {"basis": "last_reported_ride", "method": "Wh/km"},
    ),
    Description(
        key="returned_pack_count",
        group="battery",
        suggested_display_precision=0,
        value=lambda s: len(s.battery.batteries),
        attributes=lambda s: {"reported_pack_count": s.battery.observations.get("battery_count")},
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
        suggested_display_precision=1,
        value=lambda s: ride_speed(s, "average_speed_m_s"),
    ),
    *(raw_description(field) for field in ENTITY_FIELDS if field.key != "seat_lock_raw"),
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
    """Opt-in parsed view, reusing the historical summary identity."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(DEBUG_STATES)
    _unrecorded_attributes = unrecorded_attributes(DEBUG_ATTRIBUTES)

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "raw_data_summary", "sensor", "profile")

    @property
    def available(self) -> bool:
        return bool(self.snapshot and self.snapshot.present and not self.coordinator._stopping)

    def _view(self) -> tuple[str, dict[str, Any]]:
        if not self.entry.options.get(CONF_DEBUG) or self.snapshot is None:
            return "debug_disabled", {}
        return debug_view(
            self.snapshot, self.coordinator, self.entry.runtime_data.models.model(self.sn)
        )

    @property
    def native_value(self) -> str:
        return self._view()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.coordinator.raw.vehicle_summary(self.sn, dt_util.utcnow())
        return {
            "format_version": 1,
            "debug_mode": bool(self.entry.options.get(CONF_DEBUG)),
            **summary,
            **self._view()[1],
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
    def native_value(self) -> str | float | datetime | None:
        return self.entity_description.value(self.snapshot) if self.snapshot else None

    @property
    def suggested_display_precision(self) -> int | None:
        description = self.entity_description
        if description.suggested_display_precision is not None:
            return description.suggested_display_precision
        if self.snapshot is None:
            return None
        if description.precision:
            return description.precision(self.snapshot)
        return source_precision(self.snapshot, description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snapshot = self.snapshot
        if snapshot is None:
            return None
        attributes = (
            self.entity_description.attributes(snapshot)
            if self.entity_description.attributes
            else {}
        )
        if self._data_group == "travel":
            received = snapshot.travel_freshness.succeeded_at
            attributes.update(
                query_month=snapshot.travel.month if snapshot.travel else None,
                received_at=received.isoformat() if received else None,
            )
            if self.entity_description.key.startswith("last_"):
                last = snapshot.travel.last_ride if snapshot.travel else None
                ride = last.ride if last else None
                lifecycle = self.coordinator.ride_lifecycles.get(self.sn)
                phase = lifecycle.phase(ride) if lifecycle and ride else "reported"
                attributes.update(
                    ride_phase=phase,
                    completion_basis="stable_successful_samples"
                    if phase == "finalized_by_policy"
                    else None,
                )
        return attributes or None


class RatedEnergySensor(NinebotEntity, SensorEntity):
    """One fixed identity for user-supplied nominal specifications."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: NinebotConfigEntry, sn: str) -> None:
        super().__init__(entry, sn, "battery_rated_energy", "sensor", "profile")

    @property
    def native_value(self) -> float | None:
        return self.entry.runtime_data.models.model(self.sn).nominal

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"basis": "user_rated_voltage_capacity", "measured": False}


DAY_FIELDS: dict[str, tuple[DayMetric, int, str | None, SensorDeviceClass | None]] = {
    "yesterday_mileage": ("distance_km", 1, UnitOfLength.KILOMETERS, SensorDeviceClass.DISTANCE),
    "today_ride_count": ("ride_count", 0, None, None),
    "yesterday_ride_count": ("ride_count", 1, None, None),
    "today_ride_duration": ("duration_s", 0, UnitOfTime.SECONDS, SensorDeviceClass.DURATION),
    "yesterday_ride_duration": ("duration_s", 1, UnitOfTime.SECONDS, SensorDeviceClass.DURATION),
    "today_ride_energy": ("energy_wh", 0, UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY),
    "yesterday_ride_energy": ("energy_wh", 1, UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY),
}


class DayStatisticsSensor(NinebotEntity, SensorEntity):
    """Small daily projection; missing windows never become zero-valued rides."""

    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str) -> None:
        super().__init__(entry, sn, key, "sensor", "travel")
        (
            self.field,
            self.days_ago,
            self._attr_native_unit_of_measurement,
            self._attr_device_class,
        ) = DAY_FIELDS[key]

    def _summary(self) -> DaySummary:
        now = dt_util.utcnow()
        day = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date() - timedelta(days=self.days_ago)
        return day_summary(self.coordinator.statistics, self.sn, day, now)

    @property
    def available(self) -> bool:
        # Historical data keeps its actual received timestamp; a failed live
        # travel request must not hide a previously verified yesterday window.
        return (
            self.coordinator.local_vehicle_available(self.sn)
            and self.coordinator.statistics.available
        )

    @property
    def native_value(self) -> float | None:
        return getattr(self._summary(), self.field)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self._summary()
        return {
            "date": summary.day.isoformat(),
            "business_timezone": BUSINESS_TIMEZONE,
            "basis": summary.distance_basis
            if self.field == "distance_km"
            else "returned_unique_rides",
            "date_assignment": "server_chart_day"
            if self.field == "distance_km" and summary.distance_basis == "server_daily_chart"
            else "ride_end_business_date",
            "received_at": summary.received_at,
            "revision": summary.revision,
            "adjacent_received_at": summary.adjacent_received_at,
            "adjacent_revision": summary.adjacent_revision,
            "ride_window_complete": summary.rides_complete,
            "availability_reason": summary.reason(self.field),
            "storage": self.coordinator.statistics.source_mode,
        }


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
            ("health_score", "score_raw", None, None),
        ]:
            identity = battery.key

            def precision(
                s: VehicleSnapshot,
                field: str = field,
                identity: str = identity,
                primary: bool = not prefix,
            ) -> int | None:
                found = (
                    current_battery(s.battery)
                    if primary
                    else identified_battery(s.battery, identity)
                )
                raw_key = {
                    "voltage": "bms_volt",
                    "temperature": "bat_temp",
                    "cycles": "bms_cycle",
                    "score_raw": "score",
                }[field]
                return dict(found.precision).get(raw_key) if found else None

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
                    "reported_cycles": display_scalar(found.cycle_raw) if found else None,
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
                    precision=precision,
                    attributes=attributes
                    if field.endswith("_raw") or key == "bms_cycles"
                    else None,
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

    def precision(snapshot: VehicleSnapshot) -> int | None:
        battery = current_battery(snapshot.battery)
        raw_key = {"voltage": "bms_volt", "temperature": "bat_temp", "cycles": "bms_cycle"}[field]
        return dict(battery.precision).get(raw_key) if battery else None

    return Description(
        key=key,
        group="battery",
        value=value,
        precision=precision,
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
        yield from (DayStatisticsSensor(entry, sn, key) for key in DAY_FIELDS)
        if entry.options.get(CONF_ESTIMATION):
            yield RatedEnergySensor(entry, sn)

    seen = async_setup_dynamic(
        hass,
        entry,
        add,
        factory,
        signature=lambda s: tuple((b.key, b.identified) for b in s.battery.batteries),
    )
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
