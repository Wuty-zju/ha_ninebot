import json
from pathlib import Path
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot import async_migrate_entry
from custom_components.ninebot.migration import legacy_parameters
from custom_components.ninebot.session import session_uid
from custom_components.ninebot.storage import ModelStorage


async def test_open_migration_preserves_id_parameters_but_not_counters(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(
        domain="ninebot",
        version=1,
        unique_id="fake-account",
        data={
            "username": "fake-account",
            "password": "synthetic-password",
            "default_scan_interval": 60,
        },
    )
    entry.add_to_hass(hass)
    path = Path(hass.config.path(".storage", f"ninebot_{entry.entry_id}_runtime"))
    path.parent.mkdir(parents=True)
    original = json.dumps(
        {
            "version": 3,
            "data": {
                "devices": {
                    "SyntheticSN": {
                        "main_battery_voltage": 72,
                        "battery_capacity": 20,
                        "outflow_total_kwh": 999,
                        "raw_state": {"token": "synthetic"},
                    }
                }
            },
        }
    )
    await hass.async_add_executor_job(path.write_text, original)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.unique_id == "fake-account"
    assert entry.data["identity_scheme"] == "open_v1"
    assert "password" not in entry.data
    assert entry.options["poll_interval"] == 60
    assert await hass.async_add_executor_job(path.read_text) == original
    store = ModelStorage(hass, entry.entry_id)
    await store.async_load()
    model = store.model("SyntheticSN")
    assert model.nominal == 1.44
    assert not model.values
    snapshot = dict(entry.data)
    assert await async_migrate_entry(hass, entry)
    assert dict(entry.data) == snapshot


async def test_fork_migration_private_copy_idempotent(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    uid = "synthetic-business"
    entry = MockConfigEntry(
        domain="ninebot",
        version=1,
        unique_id=uid,
        data={"username": "fake-account", "business_uid": uid},
    )
    entry.add_to_hass(hass)
    source = tmp_path / ".storage" / "ninebot" / uid
    source.mkdir(parents=True)
    raw = json.dumps({"business_uid": uid, "access_token": "synthetic"})
    (source / "tokens.json").write_text(raw)
    (source / "config.json").write_text("{}")
    (source / "vehicles.json").write_text("[]")
    (source / "unexpected.txt").write_text("must not be copied")
    assert await async_migrate_entry(hass, entry)
    destination = tmp_path / ".storage" / "ninebot_v2" / entry.data["session_key"]
    assert session_uid(destination) == uid
    assert (source / "tokens.json").read_text() == raw
    assert not (destination / "unexpected.txt").exists()
    first = dict(entry.data)
    assert await async_migrate_entry(hass, entry)
    assert dict(entry.data) == first


async def test_missing_session_migrates_to_explicit_reauth_not_login(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(
        domain="ninebot",
        version=1,
        unique_id="synthetic",
        data={"business_uid": "synthetic", "username": "fake-account", "password": "synthetic"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.ninebot.session.NinecliClient") as client:
        assert await async_migrate_entry(hass, entry)
        client.assert_not_called()
    assert entry.data["business_uid"] == "synthetic"
    assert "password" not in entry.data
    newer = MockConfigEntry(domain="ninebot", version=3)
    assert not await async_migrate_entry(hass, newer)


def test_legacy_parameter_reader_rejects_raw_and_invalid_layouts(tmp_path):
    path = tmp_path / "legacy"
    assert legacy_parameters(path) == {}
    for data in ([], {}, {"data": []}, {"data": {"devices": []}}):
        path.write_text(json.dumps(data))
        assert legacy_parameters(path) == {}
    path.write_text(
        json.dumps(
            {
                "data": {
                    "devices": {
                        "one": {"main_battery_voltage": "NaN", "battery_capacity": False},
                        "bad": [],
                    }
                }
            }
        )
    )
    assert legacy_parameters(path) == {"one": {}}
