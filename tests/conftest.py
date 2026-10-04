"""Synthetic integration fixtures; no access to the user's running HA."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def app_client():
    client = AsyncMock()
    client.vehicle_discovery_complete = True
    client.async_list_vehicles.return_value = [
        {"wnumber": "SyntheticSN", "device_name": "Scooter", "vehicle_name": "Test"}
    ]
    client.async_get_status.return_value = {
        "dump_energy": 80,
        "charging": 0,
        "pwr": 1,
        "precise_estimate_mileage": 90,
        "loc": {"lock": 1, "lat": 0, "lon": 0},
    }
    client.async_get_battery.return_value = {
        "have_bms_cycle_support": False,
        "battery_list": [{"bms_volt": "75.3", "bat_temp": 25, "bms_cycle": 100}],
    }
    client.async_get_travel.return_value = {"total_mileages": 0, "ec": 0, "list": None}
    with (
        patch("custom_components.ninebot.NinecliClient", return_value=client),
        patch("custom_components.ninebot.session_uid", return_value="synthetic-business"),
    ):
        yield client


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="ninebot",
        version=2,
        data={
            "business_uid": "synthetic-business",
            "session_key": "a" * 32,
            "identity_scheme": "v2",
            "account": "fake-account",
        },
        unique_id="synthetic-business",
    )
    entry.add_to_hass(hass)
    return entry
