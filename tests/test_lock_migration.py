"""Cross-domain conversion uses public registries and preserves historical rows."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.ninebot.identity import IdentityStore
from custom_components.ninebot.models import VehicleProfile


async def prepare(hass, entry):
    store = IdentityStore(hass, entry)
    await store.async_load()
    await store.async_prepare([VehicleProfile("SyntheticSN", "Scooter", "MzMIX")])
    return store


def old_row(hass, entry, domain="binary_sensor", suffix="unlocked"):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}, name="Scooter"
    )
    return er.async_get(hass).async_get_or_create(
        domain,
        "ninebot",
        f"SyntheticSN_{suffix}",
        config_entry=entry,
        device_id=device.id,
    )


@pytest.mark.parametrize(
    "domain,suffix,key",
    [("binary_sensor", "unlocked", "vehicle_lock"), ("sensor", "seat_lock_raw", "seat_lock")],
)
async def test_conversion_preserves_uid_and_user_choices_without_duplicate(
    hass, entry, domain, suffix, key
):
    registry = er.async_get(hass)
    old = old_row(hass, entry, domain, suffix)
    old = registry.async_update_entity(
        old.entity_id,
        name="Personal lock",
        icon="mdi:lock",
        disabled_by=er.RegistryEntryDisabler.USER,
        hidden_by=er.RegistryEntryHider.USER,
        aliases=type(old.aliases)(["My lock"]),
        labels={"personal"},
    )
    store = await prepare(hass, entry)
    await store.async_convert_locks([(old, "SyntheticSN", key)])
    uid, blocked = store.lock_identity("SyntheticSN", key)
    assert uid == old.unique_id and not blocked
    new = registry.async_get(store.seeds["SyntheticSN"].entity_id("lock", key))
    assert new.id != old.id and new.unique_id == old.unique_id
    assert new.device_id == old.device_id and new.name == "Personal lock"
    assert (
        new.disabled_by is er.RegistryEntryDisabler.USER
        and new.hidden_by is er.RegistryEntryHider.USER
    )
    assert new.labels == old.labels and new.aliases == old.aliases
    assert registry.async_get(old.entity_id) is None
    exported = store.export("SyntheticSN")
    assert exported["migrations"][0]["old"] == old.entity_id
    assert exported["migrations"][0]["new"] == new.entity_id
    restarted = await prepare(hass, entry)
    await restarted.async_convert_locks([])
    assert restarted.lock_identity("SyntheticSN", key) == (uid, False)
    assert len([row for row in registry.entities.values() if row.domain == "lock"]) == 1


async def test_failed_intent_write_does_not_create_or_remove_a_row(hass, entry):
    old = old_row(hass, entry)
    store = await prepare(hass, entry)
    with patch.object(store, "_save", side_effect=OSError("synthetic disk full")):
        with pytest.raises(OSError):
            await store.async_convert_locks([(old, "SyntheticSN", "vehicle_lock")])
    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id).id == old.id
    assert not store.migrations
    assert registry.async_get(store.seeds["SyntheticSN"].entity_id("lock", "vehicle_lock")) is None


async def test_interrupted_final_write_recovers_from_created_replacement(hass, entry):
    old = old_row(hass, entry)
    store = await prepare(hass, entry)
    save = store._save
    calls = 0

    async def write():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic final write")
        await save()

    with patch.object(store, "_save", side_effect=write):
        with pytest.raises(OSError):
            await store.async_convert_locks([(old, "SyntheticSN", "vehicle_lock")])
    restarted = await prepare(hass, entry)
    await restarted.async_convert_locks([])
    assert restarted.lock_identity("SyntheticSN", "vehicle_lock") == (old.unique_id, False)
    assert er.async_get(hass).async_get(old.entity_id) is None


async def test_registry_lag_replays_exact_old_snapshot_without_losing_preferences(hass, entry):
    registry = er.async_get(hass)
    old = old_row(hass, entry)
    store = await prepare(hass, entry)
    await store.async_convert_locks([(old, "SyntheticSN", "vehicle_lock")])
    target = store.seeds["SyntheticSN"].entity_id("lock", "vehicle_lock")
    # Test-only restoration of the earlier registry atomic snapshot.
    registry.entities.pop(target)
    registry.entities[old.entity_id] = old
    restarted = await prepare(hass, entry)
    await restarted.async_convert_locks([(old, "SyntheticSN", "vehicle_lock")])
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(target).unique_id == old.unique_id


async def test_collision_and_two_legacy_identities_do_not_choose_arbitrarily(hass, entry):
    first = old_row(hass, entry)
    second = old_row(hass, entry, suffix="vehicle_lock")
    store = await prepare(hass, entry)
    await store.async_convert_locks(
        [(first, "SyntheticSN", "vehicle_lock"), (second, "SyntheticSN", "vehicle_lock")]
    )
    registry = er.async_get(hass)
    assert registry.async_get(first.entity_id) and registry.async_get(second.entity_id)
    assert store.lock_identity("SyntheticSN", "vehicle_lock") == (None, True)
    registry.async_remove(second.entity_id)  # An isolated user resolving the ambiguity.
    await store.async_convert_locks([(first, "SyntheticSN", "vehicle_lock")])
    assert store.lock_identity("SyntheticSN", "vehicle_lock") == (first.unique_id, False)
    assert ir.async_get(hass).async_get_issue("ninebot", store._issue) is None


async def test_occupied_target_and_user_change_during_journal_are_protected(hass, entry):
    registry = er.async_get(hass)
    old = old_row(hass, entry)
    store = await prepare(hass, entry)
    target = store.seeds["SyntheticSN"].entity_id("lock", "vehicle_lock")
    hass.states.async_set(target, "occupied")
    await store.async_convert_locks([(old, "SyntheticSN", "vehicle_lock")])
    assert registry.async_get(old.entity_id).id == old.id
    assert store.lock_identity("SyntheticSN", "vehicle_lock") == (None, True)
    hass.states.async_remove(target)
    save = store._save
    # Preserve a user's rename instead of retiring the changed identity.
    registry.async_update_entity(old.entity_id, new_entity_id="binary_sensor.personal")
    with patch.object(store, "_save", wraps=save):
        await store.async_convert_locks([])
    assert registry.async_get("binary_sensor.personal").id == old.id
    assert registry.async_get(target) is None


async def test_cross_domain_preserves_old_recorder_data_with_separate_new_history(
    recorder_mock, hass, entry
):
    from homeassistant.components.recorder.history import get_significant_states
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    old = old_row(hass, entry)
    start = dt_util.utcnow() - timedelta(seconds=5)
    hass.states.async_set(old.entity_id, "on", {"device_class": "lock"})
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    store = await prepare(hass, entry)
    await store.async_convert_locks([(old, "SyntheticSN", "vehicle_lock")])
    new_id = store.seeds["SyntheticSN"].entity_id("lock", "vehicle_lock")
    hass.states.async_set(new_id, "unlocked")
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    history = await recorder_mock.async_add_executor_job(
        get_significant_states,
        hass,
        start,
        dt_util.utcnow() + timedelta(seconds=1),
        [old.entity_id, new_id],
    )
    assert any(state.state == "on" for state in history[old.entity_id])
    assert any(state.state == "unlocked" for state in history[new_id])
    assert not any(state.state == "on" for state in history[new_id])
