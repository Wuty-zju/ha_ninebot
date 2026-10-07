"""Frozen naming, real HA registries and Recorder; synthetic accounts only."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot.compat import device_by_identifier
from custom_components.ninebot.identity import (
    IDENTITY_INITIALIZED,
    IDENTITY_VERSION,
    IdentityStore,
    ascii_slug,
    scoped_uid,
)
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.services import resolve_vehicle

PROFILE = VehicleProfile("SyntheticSN", "Scooter", "MzMIX")


async def prepared(hass, entry):
    store = IdentityStore(hass, entry)
    await store.async_load()
    await store.async_prepare([PROFILE])
    return store


def row(hass, entry, key="battery", custom=None):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", PROFILE.sn)}, name="Scooter"
    )
    registry = er.async_get(hass)
    name = "Battery" if key == "battery" else key.replace("_", " ").title()
    registered = registry.async_get_or_create(
        "sensor",
        "ninebot",
        f"{PROFILE.sn}_{key}",
        config_entry=entry,
        device_id=device.id,
        original_name=name,
        has_entity_name=True,
    )
    # calculated_object_id was removed in recent Core. Use the same public
    # rename API as a user/old registry snapshot to prepare a generated ID.
    registered = registry.async_update_entity(
        registered.entity_id, new_entity_id=custom or f"sensor.scooter_{key}"
    )
    return registered


async def test_frozen_seed_survives_model_account_nickname_and_restart(hass, entry):
    store = await prepared(hass, entry)
    seed = store.seeds[PROFILE.sn]
    account = ascii_slug(entry.data["account"], "account", 40)
    assert (
        seed.entity_id("sensor", "charging_power_raw")
        == f"sensor.{account}_mzmix_syntheticsn_charging_power"
    )
    hass.config_entries.async_update_entry(entry, data={**entry.data, "account": "renamed"})
    await store.async_prepare([replace(PROFILE, model="Another", name="New name")])
    restarted = await prepared(hass, entry)
    assert restarted.seeds[PROFILE.sn] == seed
    assert store.diagnostics() == {"seed_count": 1, "migration_count": 0, "statuses": {}}
    assert ascii_slug("中文", "vehicle").isascii()
    assert ascii_slug("a" * 100, "vehicle") != ascii_slug("a" * 99 + "b", "vehicle")
    assert ascii_slug("车A", "vehicle") != ascii_slug("车B", "vehicle")
    assert ascii_slug("a-b", "account") != ascii_slug("a_b", "account")


async def test_generated_rename_preserves_registry_settings_and_custom_protection(hass, entry):
    original = row(hass, entry)
    registry = er.async_get(hass)
    original = registry.async_update_entity(
        original.entity_id, name="My charge", disabled_by=er.RegistryEntryDisabler.USER
    )
    custom = row(hass, entry, "bms_voltage", "sensor.personal_voltage")
    store = await prepared(hass, entry)
    await store.async_migrate(
        [(original, PROFILE.sn, "battery"), (custom, PROFILE.sn, "bms_voltage")]
    )
    new = registry.async_get(store.seeds[PROFILE.sn].entity_id("sensor", "battery"))
    assert new.id == original.id and new.unique_id == original.unique_id
    assert new.name == "My charge" and new.disabled_by is er.RegistryEntryDisabler.USER
    assert new.device_id == original.device_id
    assert registry.async_get("sensor.personal_voltage").id == custom.id
    assert store.migrations[custom.id]["status"] == "protected"
    await store.async_migrate([(new, PROFILE.sn, "battery"), (custom, PROFILE.sn, "bms_voltage")])
    assert store.migrations[new.id]["status"] == "renamed"
    exported = store.export(PROFILE.sn)
    assert len(exported["migrations"]) == 2
    assert "password" not in str(exported) and "token" not in str(exported)


async def test_duplicate_and_occupied_targets_preflight_without_deleting(hass, entry):
    first = row(hass, entry)
    second = row(hass, entry, "remaining_range")
    store = await prepared(hass, entry)
    await store.async_migrate([(first, PROFILE.sn, "battery"), (second, PROFILE.sn, "battery")])
    assert {r["status"] for r in store.migrations.values()} == {"conflict"}
    assert er.async_get(hass).async_get(first.entity_id).id == first.id
    assert ir.async_get(hass).async_get_issue("ninebot", f"entity_id_migration_{entry.entry_id}")


async def test_interrupted_rename_is_recovered_and_user_changes_are_not_overwritten(hass, entry):
    original = row(hass, entry)
    store = await prepared(hass, entry)
    save = store._save
    calls = 0

    async def fail_after_rename():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic disk")
        await save()

    with patch.object(store, "_save", side_effect=fail_after_rename):
        with pytest.raises(OSError):
            await store.async_migrate([(original, PROFILE.sn, "battery")])
    restarted = await prepared(hass, entry)
    new = er.async_get(hass).async_get(store.seeds[PROFILE.sn].entity_id("sensor", "battery"))
    await restarted.async_migrate([(new, PROFILE.sn, "battery")])
    assert restarted.migrations[new.id]["status"] == "renamed"
    custom = er.async_get(hass).async_update_entity(
        new.entity_id, new_entity_id="sensor.my_new_name"
    )
    await restarted.async_migrate([(custom, PROFILE.sn, "battery")])
    assert er.async_get(hass).async_get("sensor.my_new_name").id == original.id


async def test_registry_delayed_persistence_restores_exact_old_row_and_resumes(hass, entry):
    original = row(hass, entry)
    store = await prepared(hass, entry)
    await store.async_migrate([(original, PROFILE.sn, "battery")])
    registry = er.async_get(hass)
    # Simulate the recorder/registry restart snapshot lagging the identity Store.
    # Test-only mutation; integration uses public API for all real renames.
    registry.entities.pop(store.seeds[PROFILE.sn].entity_id("sensor", "battery"))
    registry.entities[original.entity_id] = original
    restarted = await prepared(hass, entry)
    await restarted.async_migrate([(original, PROFILE.sn, "battery")])
    assert (
        registry.async_get(store.seeds[PROFILE.sn].entity_id("sensor", "battery")).id == original.id
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 2},
        {"version": True},
        {"version": 1, "seeds": [], "migrations": {}},
        {"version": 1, "seeds": {}, "migrations": {"bad": {"status": "planned"}}},
    ],
)
async def test_invalid_identity_store_does_not_guess_new_seeds(hass, entry, raw):
    store = IdentityStore(hass, entry)
    await store.store.async_save(raw)
    with pytest.raises(ConfigEntryError):
        await store.async_load()
    assert ir.async_get(hass).async_get_issue("ninebot", f"entity_id_migration_{entry.entry_id}")


async def test_two_accounts_same_sn_have_distinct_devices_uids_and_action_routing(
    hass, entry, app_client, enable_custom_integrations
):
    hass.config_entries.async_update_entry(entry, data={**entry.data, IDENTITY_VERSION: 1})
    other = MockConfigEntry(
        domain="ninebot",
        version=2,
        unique_id="second-business",
        data={
            **entry.data,
            "business_uid": "second-business",
            "session_key": "b" * 32,
            "account": "second-account",
        },
    )
    other.add_to_hass(hass)
    with patch(
        "custom_components.ninebot.session_uid",
        side_effect=lambda path: (
            "second-business" if path.name == "b" * 32 else "synthetic-business"
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        from homeassistant.config_entries import ConfigEntryState

        if other.state is ConfigEntryState.NOT_LOADED:
            assert await hass.config_entries.async_setup(other.entry_id)
        assert other.state is ConfigEntryState.LOADED
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    original = registry.async_get_entity_id(
        "sensor", "ninebot", scoped_uid(entry, PROFILE.sn, "battery")
    )
    second = registry.async_get_entity_id(
        "sensor", "ninebot", scoped_uid(other, PROFILE.sn, "battery")
    )
    assert (
        original != second
        and registry.async_get(original).device_id != registry.async_get(second).device_id
    )
    for owner, entity_id in ((entry, original), (other, second)):
        device_id = registry.async_get(entity_id).device_id
        assert resolve_vehicle(hass, device_id) == (owner, PROFILE.sn)
        response = await hass.services.async_call(
            "ninebot",
            "get_entity_migration",
            {"device_id": device_id},
            blocking=True,
            return_response=True,
        )
        assert response["object_prefix"].startswith(owner.data["account"].replace("-", "_"))
    app_client.async_control.assert_not_awaited()
    assert await hass.config_entries.async_unload(other.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_device_lookup_detects_api_capability_and_ownership():
    old = type("Old", (), {"async_get_device": lambda self, *, identifiers: identifiers})()
    new = type(
        "New",
        (),
        {
            "async_get_device": lambda self, *, identifiers, config_entry_id: (
                identifiers,
                config_entry_id,
            )
        },
    )()
    assert device_by_identifier(old, "entry", "sn") == {("ninebot", "sn")}
    assert device_by_identifier(new, "entry", "sn") == ({("ninebot", "sn")}, "entry")


async def test_real_recorder_history_follows_same_registry_row_rename(recorder_mock, hass, entry):
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    original = row(hass, entry)
    start = dt_util.utcnow() - timedelta(seconds=5)
    hass.states.async_set(
        original.entity_id, "88", {"state_class": "measurement", "unit_of_measurement": "%"}
    )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    from functools import partial

    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics, get_metadata

    async_import_statistics(
        hass,
        {
            "statistic_id": original.entity_id,
            "source": "recorder",
            "name": "Battery",
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "unit_class": None,
            "unit_of_measurement": "%",
        },
        [
            {
                "start": start.replace(minute=0, second=0, microsecond=0),
                "mean": 88,
                "min": 88,
                "max": 88,
            }
        ],
    )
    await async_wait_recording_done(hass)
    before_metadata = await recorder_mock.async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={original.entity_id})
    )
    store = await prepared(hass, entry)
    await store.async_migrate([(original, PROFILE.sn, "battery")])
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    target = store.seeds[PROFILE.sn].entity_id("sensor", "battery")
    result = await recorder_mock.async_add_executor_job(
        get_significant_states, hass, start, dt_util.utcnow() + timedelta(seconds=1), [target]
    )
    assert any(state.state == "88" for state in result[target])
    assert er.async_get(hass).async_get(target).id == original.id
    metadata = await recorder_mock.async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={target, original.entity_id})
    )
    assert original.entity_id not in metadata
    assert metadata[target][0] == before_metadata[original.entity_id][0]


async def test_occupied_target_and_failed_journal_cannot_rename_registry(hass, entry):
    from homeassistant.helpers.storage import Store
    from homeassistant.util.file import WriteError

    original = row(hass, entry)
    store = await prepared(hass, entry)
    with patch.object(Store, "_async_write_data", side_effect=WriteError("disk full")):
        with pytest.raises(OSError):
            await store.async_migrate([(original, PROFILE.sn, "battery")])
    registry = er.async_get(hass)
    assert registry.async_get(original.entity_id).id == original.id
    # Retrying must commit the existing intent before performing the rename.
    target = store.seeds[PROFILE.sn].entity_id("sensor", "battery")
    hass.states.async_set(target, "occupied")
    await store.async_migrate([(original, PROFILE.sn, "battery")])
    assert registry.async_get(original.entity_id).id == original.id
    assert store.migrations[original.id]["status"] == "conflict"
    assert registry.async_get(f"{target}_2") is None


async def test_missing_initialized_store_and_foreign_device_namespace_are_rejected(hass, entry):
    hass.config_entries.async_update_entry(entry, data={**entry.data, IDENTITY_INITIALIZED: True})
    store = IdentityStore(hass, entry)
    with pytest.raises(ConfigEntryError):
        await store.async_load()
    await store.store.async_save(
        {
            "version": 1,
            "seeds": {
                PROFILE.sn: {
                    "object_prefix": "account_vehicle_serial",
                    "device_identifier": "account:foreign:other-serial",
                    "legacy_uids": False,
                }
            },
            "migrations": {},
        }
    )
    with pytest.raises(ConfigEntryError):
        await store.async_load()


async def test_migration_capacity_aborts_before_registry_mutation(hass, entry):
    original = row(hass, entry)
    store = await prepared(hass, entry)
    with patch("custom_components.ninebot.identity.MAX_MIGRATIONS", 0):
        with pytest.raises(ConfigEntryError):
            await store.async_migrate([(original, PROFILE.sn, "battery")])
    assert er.async_get(hass).async_get(original.entity_id).id == original.id
