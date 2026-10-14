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
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    registry = er.async_get(hass)
    for key in ("history_pause", "history_resume"):
        entity_id = registry.async_get_entity_id("button", "ninebot", f"{SN}_{key}")
        assert registry.async_get(entity_id).disabled_by is None
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
        assert await co.statistics.archive.async_history_paused(SN) is (key == "history_pause")
        assert co.preservation.progress[SN].state == (
            "paused" if key == "history_pause" else "disabled"
        )
    entity_id = registry.async_get_entity_id("button", "ninebot", f"{SN}_history_sync")
    with patch.object(co.preservation.bootstrap, "async_step", AsyncMock(return_value=False)):
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    assert await co.statistics.archive.async_history_requested(SN)
    assert await co.statistics.archive.async_history_plan(SN) is not None
    app_client.async_control.assert_not_awaited()
