"""Synthetic ride observations, a native HA blueprint and real WebSocket delivery."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component

from custom_components.ninebot.const import CONF_COORDINATES
from custom_components.ninebot.ride_receipt import EVENT_RIDE_RECEIPT, async_open_ride_receipt

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 9, 26, tzinfo=UTC)
SN = "SyntheticSN"
PHONE = "mobile_app_synthetic_phone"
BLUEPRINT = Path(__file__).parents[1] / "blueprints/automation/ninebot/ride_receipt.yaml"
REAL_WRITE = Store._async_write_data
REAL_LOAD = Store._async_load


@pytest.fixture
async def receipt_setup(
    hass, entry, app_client, freezer, tmp_path, hass_ws_client, hass_admin_user
):
    hass.config.config_dir = str(tmp_path)
    freezer.move_to(NOW)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_COORDINATES: True})
    app_client.async_get_travel.return_value = {"times": 0, "list": []}
    receipts = []
    notifications = []

    @callback
    def receipt(event):
        receipts.append(event.data)

    @callback
    def notify(call):
        notifications.append(call.data)

    hass.bus.async_listen(EVENT_RIDE_RECEIPT, receipt)
    hass.services.async_register("notify", PHONE, notify)
    with (
        patch.object(Store, "_async_write_data", REAL_WRITE),
        patch.object(Store, "_async_load", REAL_LOAD),
        patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        event_id = er.async_get(hass).async_get_entity_id("event", "ninebot", f"{SN}_ride")
        assert event_id and hass.states.get(event_id)
        blueprint_path = tmp_path / "blueprints/automation/ninebot/ride_receipt.yaml"

        def install_blueprint():
            blueprint_path.parent.mkdir(parents=True)
            blueprint_path.write_bytes(BLUEPRINT.read_bytes())

        await hass.async_add_executor_job(install_blueprint)
        assert await async_setup_component(
            hass,
            "automation",
            {
                "automation": [
                    {
                        "alias": "Send synthetic receipt",
                        "use_blueprint": {
                            "path": "ninebot/ride_receipt.yaml",
                            "input": {
                                "ride_event": event_id,
                                "mobile_action": f"notify.{PHONE}",
                            },
                        },
                    }
                ]
            },
        )
        assert await async_setup_component(hass, "websocket_api", {})
        websocket_api.async_register_command(hass, async_open_ride_receipt)
        await hass.async_block_till_done()
        assert hass.states.get("automation.send_synthetic_receipt").state == "on"

        async def connect():
            # Advancing the synthetic clock can close a socket's heartbeat.
            refresh_token = await hass.auth.async_create_refresh_token(
                hass_admin_user, "https://test.invalid"
            )
            return await hass_ws_client(
                hass, access_token=hass.auth.async_create_access_token(refresh_token)
            )

        yield connect, event_id, receipts, notifications


def cloud_ride(identity, end=120, *, distance=0.3, energy=13):
    ended = int(NOW.timestamp()) + end
    return {
        "travel_id": identity,
        "start_time": ended - 60,
        "end_time": ended,
        "duration": 60,
        "mileages": distance,
        "ec": energy,
    }


async def sample(hass, entry, app_client, freezer, seconds, *rides):
    co = entry.runtime_data.coordinator
    freezer.move_to(NOW + timedelta(seconds=seconds))
    app_client.async_get_travel.return_value = {"times": len(rides), "list": list(rides)}
    await co._group(SN, "travel", force=True)
    co.async_set_updated_data(dict(co.data))
    await hass.async_block_till_done()


async def open_receipt(client, locator, identity=1):
    await client.send_json(
        {"id": identity, "type": "ninebot/open_ride_receipt", "receipt": locator}
    )
    return await asyncio.wait_for(client.receive_json(), 5)


async def test_blueprint_delivers_one_ride_then_replaces_and_reopens_its_current_version(
    hass, entry, app_client, freezer, receipt_setup
):
    connect, event_id, receipts, notifications = receipt_setup
    ride = cloud_ride("synthetic-completed-ride")
    await sample(hass, entry, app_client, freezer, 120, ride)
    assert not notifications
    await sample(hass, entry, app_client, freezer, 720, ride)
    assert [row["phase"] for row in receipts] == ["completed"]
    assert len(notifications) == 1
    first = notifications[0]["data"]
    assert first["url"] == first["clickAction"] == first["actions"][0]["uri"]
    assert first["url"].startswith("/ninebot-archive?receipt=r1.")
    assert ride["travel_id"] not in first["url"]
    assert "0.3 km" in notifications[0]["message"] and "13.0 Wh" in notifications[0]["message"]
    assert set(receipts[0]) == {
        "phase",
        "entity_id",
        "vehicle_label",
        "distance_m",
        "duration_s",
        "energy_wh",
        "tag",
        "replay_url",
    }
    assert first["push"]["interruption-level"] == "active"

    locator = first["url"].split("=", 1)[1]
    original = (await open_receipt(await connect(), locator))["result"]["rides"]
    assert len(original) == 1 and original[0]["ride_id"] == ride["travel_id"]
    assert original[0]["distance_m"] == 300

    revised = {**ride, "mileages": 0.4, "ec": 17}
    late = cloud_ride("synthetic-late-ride", end=60)
    await sample(hass, entry, app_client, freezer, 780, revised, late)
    assert len(notifications) == 1
    await sample(hass, entry, app_client, freezer, 1380, revised, late)
    assert hass.states.get(event_id).attributes["event_type"] == "late_reported"
    assert [row["phase"] for row in receipts] == ["completed", "corrected"]
    assert len(notifications) == 2
    second = notifications[1]["data"]
    assert first["tag"] == second["tag"]
    assert second["push"]["interruption-level"] == "passive"
    assert second["importance"] == "low" and second["alert_once"]
    assert "0.4 km" in notifications[1]["message"] and "17.0 Wh" in notifications[1]["message"]
    assert (await open_receipt(await connect(), locator))["result"]["rides"][0]["distance_m"] == 400

    offline_revision = {**revised, "mileages": 0.45}
    missed = cloud_ride("synthetic-missed-during-reload", end=1320)
    app_client.async_get_travel.return_value = {
        "times": 3,
        "list": [offline_revision, late, missed],
    }
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await sample(hass, entry, app_client, freezer, 1980, offline_revision, late, missed)
    assert len(notifications) == 2
    app_client.reset_mock()
    client = await connect()
    current = (await open_receipt(client, locator, 2))["result"]["rides"]
    assert len(current) == 1 and current[0]["distance_m"] == 450 and current[0]["energy_wh"] == 17
    assert current[0]["ride_id"] == ride["travel_id"] and current[0]["query_month"] == "202609"
    assert current[0]["detail_saved"] is False
    assert {"track", "latitude", "longitude", "raw"}.isdisjoint(current[0])
    assert (
        not app_client.async_get_trip_detail.await_count
        and not app_client.async_get_travel.await_count
    )
    # If a later complete page retires the ride, the old URL no longer resolves.
    await sample(hass, entry, app_client, freezer, 1981, late)
    assert (await open_receipt(client, locator, 3))["error"]["code"] == "unavailable"


async def test_receipt_rechecks_ha_permissions_and_ownership_before_delivery(
    hass, entry, app_client, freezer, receipt_setup, monkeypatch
):
    connect, _, receipts, _ = receipt_setup
    ride = cloud_ride("synthetic-permission-ride")
    await sample(hass, entry, app_client, freezer, 120, ride)
    await sample(hass, entry, app_client, freezer, 720, ride)
    client = await connect()
    locator = receipts[0]["replay_url"].split("=", 1)[1]
    co = entry.runtime_data.coordinator
    archive = co.statistics.archive
    original = archive.async_current_detail_summary
    read = AsyncMock(wraps=original)
    monkeypatch.setattr(archive, "async_current_detail_summary", read)
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    allowed = {"calendar"}
    user.permissions.check_entity.side_effect = lambda eid, _: eid.split(".")[0] in allowed
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        assert (await open_receipt(client, locator))["error"]["code"] == "unauthorized"
        read.assert_not_awaited()
        allowed.symmetric_difference_update({"calendar", "device_tracker"})
        assert (await open_receipt(client, locator, 2))["error"]["code"] == "unauthorized"
        read.assert_not_awaited()
        allowed.add("calendar")
        assert (await open_receipt(client, locator, 3))["success"]
        read.assert_awaited_once()

        async def revoke_tracker(*args, **kwargs):
            result = await original(*args, **kwargs)
            allowed.remove("device_tracker")
            return result

        monkeypatch.setattr(archive, "async_current_detail_summary", revoke_tracker)
        assert (await open_receipt(client, locator, 4))["error"]["code"] == "unauthorized"
        allowed.add("device_tracker")

        async def change_ownership(*args, **kwargs):
            result = await original(*args, **kwargs)
            co._ownership[SN] = co._ownership.get(SN, 0) + 1
            return result

        monkeypatch.setattr(archive, "async_current_detail_summary", change_ownership)
        assert (await open_receipt(client, locator, 5))["error"]["code"] == "unavailable"

    altered = locator[:-2] + ("AA" if not locator.endswith("AA") else "BB")
    assert (await open_receipt(client, altered, 6))["error"]["code"] == "unavailable"
