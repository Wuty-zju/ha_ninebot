import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import adapters
from custom_components.ninebot.models import VehicleProfile, VehicleSnapshot
from custom_components.ninebot.sensor import SENSORS, NinebotSensor, last_timed_ride, ride_speed

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"
KEYS = {
    "last_ride_duration",
    "last_ride_start",
    "last_ride_end",
    "last_ride_max_speed",
    "last_ride_average_speed",
}


async def test_last_ride_native_values_classes_disabled_defaults_and_state_size(
    hass, entry, app_client, freezer
):
    freezer.move_to(datetime(2026, 9, 26, tzinfo=UTC))
    month = json.loads((FIXTURES / "travel-nonempty.json").read_text())
    app_client.async_get_travel.return_value = month
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    descriptions = {description.key: description for description in SENSORS}
    expected = {
        "last_ride_duration": (83, "duration", "s"),
        "last_ride_start": (datetime(2026, 9, 25, 8, tzinfo=UTC), "timestamp", None),
        "last_ride_end": (datetime(2026, 9, 25, 8, 1, 23, tzinfo=UTC), "timestamp", None),
        "last_ride_max_speed": (23, "speed", "km/h"),
        "last_ride_average_speed": (300 / 83 * 3.6, "speed", "km/h"),
    }
    for key, (value, device_class, unit) in expected.items():
        row = registry.async_get(
            registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}")
        )
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert row.unit_of_measurement == unit and row.original_device_class == device_class
        assert hass.states.get(row.entity_id) is None
        sensor = NinebotSensor(entry, "SyntheticSN", descriptions[key])
        assert sensor.available and sensor.has_entity_name
        assert (
            sensor.native_value == pytest.approx(value)
            if isinstance(value, float)
            else sensor.native_value == value
        )
        assert sensor.state_class is None and sensor.extra_state_attributes is None
        assert sensor.translation_key == key
        registry.async_update_entity(row.entity_id, disabled_by=None)
    # Isolated HA reload registers enabled entities; no production runtime is touched.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    start = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_last_ride_start")
    assert hass.states.get(start).state == "2026-09-25T08:00:00+00:00"
    for key in KEYS:
        entity_id = registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}")
        attributes = hass.states.get(entity_id).attributes
        for forbidden in ("raw", "rides", "track", "latitude", "longitude", "speed_samples"):
            assert forbidden not in attributes
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


def test_unknown_order_future_and_conflicting_times_do_not_create_new_values():
    profile = VehicleProfile("synthetic", "name", "model")
    old = VehicleSnapshot(profile, travel=adapters.travel({"list": [{"mileages": 0.3}]}, "202609"))
    assert old.travel.last_ride.mileage == 0.3
    assert last_timed_ride(old) is None
    assert ride_speed(old, "server_max_speed_m_s") is None
    assert all(description.value(old) is None for description in SENSORS if description.key in KEYS)
    assert last_timed_ride(VehicleSnapshot(profile)) is None
    month = json.loads((FIXTURES / "travel-nonempty.json").read_text())
    current = VehicleSnapshot(profile, travel=adapters.travel(month, "202609"))
    ride = current.travel.last_ride.ride
    assert last_timed_ride(current) is not None
    for changed in (
        replace(ride, ended_at=datetime.now(UTC) + timedelta(days=1)),
        replace(ride, ended_at=None),
        replace(ride, issues=("conflicting_time_representations",)),
    ):
        invalid = replace(
            current,
            travel=replace(
                current.travel, last_ride=replace(current.travel.last_ride, ride=changed)
            ),
        )
        assert last_timed_ride(invalid) is None
    different_duration = replace(
        current,
        travel=replace(
            current.travel,
            last_ride=replace(
                current.travel.last_ride, ride=replace(ride, issues=("duration_time_difference",))
            ),
        ),
    )
    assert ride_speed(different_duration, "average_speed_m_s") is None
    assert ride_speed(different_duration, "server_max_speed_m_s") == 23
