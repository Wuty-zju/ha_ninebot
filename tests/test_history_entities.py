"""History UI uses local cached scalars; commands retain scoped Action behavior."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.detail_health import HistoryHealth
from custom_components.ninebot.history_entities import HISTORY_SENSORS, HistoryProgressSensor
from custom_components.ninebot.history_plan import HistoryPlan
from custom_components.ninebot.history_progress import HistoryEvidence, project_progress

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
SN = "SyntheticSN"
NOW = datetime(2026, 10, 14, tzinfo=UTC)


async def test_history_sensors_are_enabled_local_and_do_not_claim_full_completion(
    hass, entry, app_client
):
    hass.config_entries.async_update_entry(entry, options={**entry.options, "debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    registry = er.async_get(hass)
    plan = HistoryPlan.create("a" * 32, "b" * 64, "202609", "202610", NOW.isoformat())
    co.preservation.progress[SN] = project_progress(
        HistoryEvidence(plan, HistoryHealth(2, 2, 0, 0, 0, 0, 0, 0, 0)),
        NOW,
        enabled=True,
        paused=False,
        authenticated=True,
    )
    co.statistics._notify_change(SN, lambda: True)
    await hass.async_block_till_done()
    state_id = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_history_sync_state")
    assert hass.states.get(state_id).state == "discovering"
    for key in HISTORY_SENSORS:
        entity_id = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_{key}")
        assert registry.async_get(entity_id).disabled_by is None
        assert hass.states.get(entity_id).attributes["discovery_complete"] is False
    details_id = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_history_detail_progress")
    assert hass.states.get(details_id).state == "100"
    with patch.object(co.statistics.archive, "async_history_evidence", AsyncMock()) as read:
        for key in HISTORY_SENSORS:
            sensor = HistoryProgressSensor(entry, SN, key)
            assert sensor.available
            _ = sensor.native_value
            assert "trail" not in sensor.extra_state_attributes
        read.assert_not_awaited()


async def test_history_buttons_pause_resume_and_sync_without_physical_controls(
    hass, entry, app_client
):
    hass.config_entries.async_update_entry(
        entry, options={"preserve_history": True, "auto_history_sync": False}
    )
    hass.config_entries.async_update_entry(entry, options={**entry.options, "debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    registry = er.async_get(hass)
    for key in ("history_pause", "history_sync"):
        entity_id = registry.async_get_entity_id("button", "ninebot", f"{SN}_{key}")
        assert registry.async_get(entity_id).disabled_by is None
        with patch.object(co.preservation.bootstrap, "async_step", AsyncMock(return_value=False)):
            await hass.services.async_call(
                "button", "press", {"entity_id": entity_id}, blocking=True
            )
        assert await co.statistics.archive.async_history_paused(SN) is (key == "history_pause")
        if key == "history_pause":
            assert co.preservation.progress[SN].state == "paused"
    entity_id = registry.async_get_entity_id("button", "ninebot", f"{SN}_history_sync")
    with patch.object(co.preservation.bootstrap, "async_step", AsyncMock(return_value=False)):
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    assert await co.statistics.archive.async_history_requested(SN)
    assert await co.statistics.archive.async_history_plan(SN) is not None
    app_client.async_control.assert_not_awaited()


async def test_uncertain_discovery_keeps_local_history_and_recovery_resumes(
    hass, entry, app_client
):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.ninebot.exceptions import ErrorKind, NinebotError
    from custom_components.ninebot.history_plan import HistoryState
    from custom_components.ninebot.raw import Endpoint, build_record
    from custom_components.ninebot.ride_archive import vehicle_key

    hass.config_entries.async_update_entry(
        entry, options={"preserve_history": True, "auto_history_sync": True}
    )
    hass.config_entries.async_update_entry(entry, options={**entry.options, "debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    now = dt_util.utcnow()
    baseline = HistoryPlan.create("c" * 32, vehicle_key(SN), "202608", "202608", now.isoformat())
    await co.statistics.archive.async_set_history_plan(SN, baseline, None, guard=lambda: True)
    await co.preservation.async_refresh_progress((SN,), now)
    registry = er.async_get(hass)
    status_id = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_history_sync_state")
    original_ownership = co._ownership.get(SN, 0)
    app_client.async_list_vehicles.return_value = []
    co._next_attempt[("", "profile")] = 0
    await co.async_refresh()
    await hass.async_block_till_done()
    assert co.local_vehicle_available(SN)
    assert co._ownership.get(SN, 0) == original_ownership
    state = hass.states.get(status_id)
    assert state.state == "waiting_connection"
    assert co.discovery_diagnostics()["complete"] is False
    for key in HISTORY_SENSORS[1:]:
        identity = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_{key}")
        assert hass.states.get(identity).state != "unavailable"
    with pytest.raises(NinebotError) as err:
        await co.async_query_history_page(SN, "202608", 1)
    assert err.value.kind is ErrorKind.CLOSED
    await co.preservation.async_tick(now)
    assert await co.statistics.archive.async_history_plan(SN) == baseline
    app_client.async_control.assert_not_awaited()

    # A later positive list is required; no timer or cached status grants ownership.
    app_client.async_list_vehicles.return_value = [{"wnumber": SN}]
    co._next_attempt[("", "profile")] = 0
    await co.async_refresh()
    await hass.async_block_till_done()
    assert co.local_vehicle_available(SN)
    assert hass.states.get(status_id).state == "discovering"
    assert await co.statistics.archive.async_history_plan(SN) == baseline
    when = dt_util.utcnow() + timedelta(seconds=3)
    response = build_record(
        Endpoint.TRAVEL,
        {"times": 0, "list": []},
        when,
        "202608",
        backend_version="0.1.7",
        endpoint_version=baseline.provider,
    )
    with patch.object(co, "async_query_history_page", AsyncMock(return_value=response)) as query:
        assert await co.preservation.bootstrap.async_step((SN,), when, 60)
    query.assert_awaited_once_with(SN, "202608", 1)
    saved = await co.statistics.archive.async_history_plan(SN)
    assert saved.plan_id == baseline.plan_id and saved.cutoff == baseline.cutoff
    assert saved.discovery_complete and saved.state is HistoryState.PRESERVE
    assert saved.verified_months == 1
    assert (
        registry.async_get_entity_id("sensor", "ninebot", f"{SN}_history_sync_state") == status_id
    )


async def test_absence_status_does_not_override_shutdown_or_identity_guard(hass, entry, app_client):
    from dataclasses import replace

    hass.config_entries.async_update_entry(entry, options={**entry.options, "debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    co.data[SN] = replace(co.data[SN], present=False)
    sensor = HistoryProgressSensor(entry, SN, "history_sync_state")
    assert sensor.available and sensor.native_value == "vehicle_not_present"
    sensor._identity_blocked = True
    assert not sensor.available
    sensor._identity_blocked = False
    co._stopping = True
    try:
        assert not sensor.available
    finally:
        co._stopping = False
    co.data.pop(SN)
    assert not sensor.available


async def test_debug_off_hides_only_debug_views_and_keeps_diagnostic_features(
    hass, entry, app_client
):
    from dataclasses import replace

    from homeassistant.helpers.entity import EntityCategory

    hass.config_entries.async_update_entry(
        entry, options={"preserve_history": True, "auto_history_sync": False}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for key in (
        "raw_data_summary",
        "history_sync_state",
        "history_sync_last_success",
        "month_list_coverage",
    ):
        entity_id = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_{key}")
        row = registry.async_get(entity_id)
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert row.hidden_by is er.RegistryEntryHider.INTEGRATION
        assert hass.states.get(entity_id) is None
    for domain, key in (
        ("sensor", "history_scan_progress"),
        ("sensor", "history_detail_progress"),
        ("calendar", "ride_calendar"),
        ("button", "history_sync"),
        ("button", "history_pause"),
    ):
        row = registry.async_get(registry.async_get_entity_id(domain, "ninebot", f"{SN}_{key}"))
        assert row.entity_category is EntityCategory.DIAGNOSTIC
        assert row.disabled_by is None
    assert registry.async_get_entity_id("button", "ninebot", f"{SN}_history_resume") is None
    tracker = registry.async_get_entity_id("device_tracker", "ninebot", f"{SN}_location")
    assert hass.states.get(tracker).name == "Scooter"
    registry.async_update_entity(tracker, name="My location")
    await hass.async_block_till_done()
    assert hass.states.get(tracker).name == "My location"
    co = entry.runtime_data.coordinator
    sync = registry.async_get_entity_id("button", "ninebot", f"{SN}_history_sync")
    for status, icon in (
        ("paused", "mdi:pause-circle-outline"),
        ("waiting_budget", "mdi:timer-sand"),
        ("preserving", "mdi:sync"),
    ):
        co.preservation.progress[SN] = replace(co.preservation.progress[SN], state=status)
        co.statistics._notify_change(SN, lambda: True)
        await hass.async_block_till_done()
        assert hass.states.get(sync).attributes["icon"] == icon
        assert hass.states.get(sync).attributes["sync_state"] == status
