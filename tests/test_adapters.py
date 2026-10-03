import math
from datetime import UTC, datetime

import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.exceptions import NinebotError
from custom_components.ninebot.models import Freshness


@pytest.mark.parametrize("raw", [None, "", "NaN", "Infinity", math.nan, math.inf, True, {}, []])
def test_invalid_number(raw):
    assert adapters.number(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, False),
        ("0", False),
        (1, True),
        ("1", True),
        (False, False),
        (True, True),
        (2, None),
        ("false", None),
        (1.0, None),
        ([], None),
    ],
)
def test_binary_domain(raw, expected):
    assert adapters.boolean(raw) is expected


def test_real_soc_zero_ranges_and_reverse_lock():
    state = adapters.status(
        {
            "dump_energy": "93",
            "precise_estimate_mileage": "104.7",
            "ai_estimate_mileage": 0,
            "charging": "0",
            "pwr": 1,
            "loc": {"lock": 1, "lat": 0, "lon": "0"},
        }
    )
    assert state.battery == 93
    assert state.range_precise == 104.7
    assert state.range_ai == 0
    assert state.locked is True
    assert state.charging is False
    assert state.latitude == state.longitude == 0
    assert adapters.status({"loc": {"lock": 0}}).locked is False
    assert adapters.status({}).battery is None
    assert adapters.status({"dump_energy": 101, "loc": {"lat": 91, "lon": 2}}).latitude is None


def test_all_batteries_and_support_gate():
    result = adapters.batteries(
        {
            "battery_count": "0",
            "battery_list": [
                {
                    "battery_sn": "synthetic-a",
                    "bms_volt": "75.3",
                    "bat_temp": 26,
                    "bms_cycle": "100",
                    "have_bms_cycle_support": False,
                },
                {
                    "battery_sn": "synthetic-b",
                    "bms_volt": 76,
                    "bat_temp": 0,
                    "bms_cycle": 100,
                    "have_bms_cycle_support": True,
                },
            ],
            "charging_power": 0,
        }
    )
    assert len(result.batteries) == 2
    assert result.batteries[0].cycles is None
    assert result.batteries[1].cycles == 100
    assert result.batteries[1].temperature == 0
    assert result.charging_power_raw == 0
    with pytest.raises(NinebotError):
        adapters.batteries({"battery_list": [{"sn": "same"}, {"sn": "same"}]})
    assert not adapters.batteries({"data": {"battery_list": [{}]}}).batteries[0].identified


def test_current_month_is_not_fallback_month():
    current = adapters.travel({"total_mileages": "0.0", "ec": 0, "list": None}, "202610")
    previous = adapters.travel(
        {"total_mileages": "303.9", "ec": 7210, "list": [{"mileages": 0.3, "ec": 5}]}, "202609"
    )
    assert current.mileage == current.energy_raw == 0
    assert current.last_ride is None
    assert previous.last_ride.mileage == 0.3
    assert adapters.previous_month("202601") == "202512"
    assert adapters.month_at(datetime(2026, 9, 30, 16, tzinfo=UTC)) == "202610"
    with pytest.raises(NinebotError):
        adapters.travel({"month": "202609"}, "202610")
    for bad in ("202613", "20260", "invalid"):
        with pytest.raises(ValueError):
            adapters.previous_month(bad)
    with pytest.raises(ValueError):
        adapters.month_at(datetime(2026, 1, 1))


def test_vehicle_identity_does_not_follow_nickname():
    raw = [
        {"wnumber": "SyntheticSN", "device_name": "fmix", "img_url": "https://example.invalid/a"}
    ]
    result = adapters.profiles(raw)
    assert result[0].sn == "SyntheticSN"
    assert adapters.profiles(raw + raw) == result
    with pytest.raises(NinebotError):
        adapters.profiles(raw + [{"wnumber": "SyntheticSN", "device_name": "other"}])
    with pytest.raises(NinebotError):
        adapters.profiles([{"device_name": "fmix"}])
    assert adapters.profiles([]) == ()
    assert adapters.profiles([{"sn": "x", "img_url": "http://bad"}])[0].image_url is None


def test_freshness_is_query_success_not_report_time():
    now = datetime(2026, 10, 3, tzinfo=UTC)
    assert not Freshness().valid(now, 30)
    assert Freshness(succeeded_at=now).valid(now, 30)
    assert not Freshness(succeeded_at=now).valid(datetime(2026, 10, 4, tzinfo=UTC), 30)


@pytest.mark.parametrize("support,expected", [(False, None), (True, 100), (None, None)])
def test_vehicle_level_cycle_support_matches_captured_app_shape(support, expected):
    result = adapters.batteries(
        {
            "have_bms_cycle_support": support,
            "battery_count": "0",
            "battery_list": [{"bms_volt": "75.3", "bat_temp": "26", "bms_cycle": "100"}],
        }
    )
    assert result.batteries[0].cycle_supported is support
    assert result.batteries[0].cycles == expected
    assert result.batteries[0].voltage == 75.3
    assert len(result.batteries) == 1


def test_vehicle_denial_and_explicit_pack_denial_both_gate_cycles():
    for vehicle, pack in ((False, True), (True, False)):
        result = adapters.batteries(
            {
                "have_bms_cycle_support": vehicle,
                "battery_list": [{"have_bms_cycle_support": pack, "bms_cycle": 100}],
            }
        )
        assert result.batteries[0].cycle_supported is False
        assert result.batteries[0].cycles is None
