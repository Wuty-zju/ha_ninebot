"""Reviewed display metadata and finite field contracts, entirely synthetic."""

import pytest

from custom_components.ninebot.account import AccountDisplay, account_update
from custom_components.ninebot.adapters import batteries, status, travel
from custom_components.ninebot.models import VehicleProfile, VehicleSnapshot
from custom_components.ninebot.raw import Endpoint
from custom_components.ninebot.sensor import SENSORS, battery_descriptions, source_precision
from custom_components.ninebot.travel import merge_detail, parse_ride


def test_display_metadata_whitelist_bounds_and_custom_title():
    display = AccountDisplay.parse(
        {"username": "Rider", "region": "bj", "phone": "private", "token": "secret"}
    )
    assert display.as_dict() == {"username": "Rider", "region": "bj"}
    assert display.title("test-account") == "Rider：test-account[bj]"
    assert "Rider" not in repr(display)
    assert AccountDisplay.parse({"username": "x" * 65, "region": "unsafe\nvalue"}).as_dict() == {}
    data = {"account_display": display.as_dict(), "automatic_title": display.title("test-account")}
    assert account_update(data, "test-account", AccountDisplay(), "My account")[2] == "My account"
    assert (
        account_update(data, "test-account", AccountDisplay("New"), data["automatic_title"])[2]
        == "New：test-account[bj]"
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        (1, "lithium"),
        ("2", "lead_acid"),
        (3, "unrecognized"),
        (None, None),
        (True, None),
        (1.5, None),
        ({}, None),
    ],
)
def test_battery_enum_does_not_guess_unrecognized_codes(code, expected):
    assert batteries({"battery_type": code}).battery_type == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("93", 93),
        (0, 0),
        (100, 100),
        (101, None),
        (-1, None),
        (93.5, None),
        (True, None),
        (None, None),
    ],
)
def test_emergency_soc_separate_from_main_pack_and_invalid_values(raw, expected):
    battery = batteries(
        {
            "battery_main": {"electricity": raw},
            "electricity": 88,
            "battery_list": [{"electricity": "88"}],
        }
    )
    assert battery.emergency_soc == expected
    assert battery.batteries[0].electricity_raw == "88"
    snapshot = VehicleSnapshot(VehicleProfile("test", "Vehicle", "Model"), battery=battery)
    description = next(d for d in SENSORS if d.key == "emergency_battery")
    assert description.value(snapshot) == expected
    assert (
        description.native_unit_of_measurement == "%"
        and description.suggested_display_precision == 0
    )


def test_existing_observation_identities_have_translated_finite_states():
    snapshot = VehicleSnapshot(
        VehicleProfile("test", "Vehicle", "Model"),
        status(
            {
                "loc": {"acc": 0},
                "pwr": 1,
                "barrel_lock_status": 0,
                "battery_exist": 1,
                "is_smart_service_expired": 0,
            }
        ),
    )
    descriptions = {d.key: d for d in SENSORS}
    assert snapshot.status.powered is True
    for key, expected in [
        ("acc_raw", "off"),
        ("seat_lock_raw", "locked"),
        ("battery_present_raw", "present"),
        ("service_expired_raw", "active"),
    ]:
        d = descriptions[key]
        assert d.value(snapshot) == expected
        assert d.entity_category is None and d.device_class == "enum"
        assert d.attributes(snapshot)["interpretation"] == "interpreted"
    unknown = VehicleSnapshot(snapshot.profile, status({"loc": {"acc": 2}}))
    assert descriptions["acc_raw"].value(unknown) == "unrecognized"


def test_source_precision_preserves_reported_scale_and_detail_corrections():
    month = travel(
        {
            "total_mileages": "14.8",
            "ec": 340,
            "list": [
                {
                    "travel_id": "test",
                    "mileages": "8.70",
                    "duration": 1384,
                    "speed": "50.00",
                    "ec": "195.00",
                }
            ],
        },
        "202610",
    )
    snapshot = VehicleSnapshot(
        VehicleProfile("test", "Vehicle", "Model"),
        status({"dump_energy": "88", "estimate_mileage": 98.3}),
        batteries({"battery_list": [{"bms_volt": "77.7", "bat_temp": "22"}]}),
        month,
    )
    assert source_precision(snapshot, "month_mileage") == 1
    assert source_precision(snapshot, "last_mileage") == 2
    assert source_precision(snapshot, "last_ride_max_speed") == 2
    assert source_precision(snapshot, "endurance") == 1
    d = next(d for d in battery_descriptions(snapshot) if d.key == "bms_voltage")
    assert d.precision(snapshot) == 1
    summary = month.rides[0]
    detail = parse_ride(
        {"travel_id": "test", "mileages": 8.7, "speed": 50, "ec": 195},
        "202610",
        source=Endpoint.TRIP_DETAIL,
    )
    assert dict(merge_detail(summary, detail).precision)["speed"] == 2
    corrected = parse_ride(
        {"travel_id": "test", "speed": "49.5"}, "202610", source=Endpoint.TRIP_DETAIL
    )
    assert dict(merge_detail(summary, corrected).precision)["speed"] == 1
