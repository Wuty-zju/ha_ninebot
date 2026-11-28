"""Real authenticated HA WebSocket contract, synthetic local archive only."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.ninebot import history_websocket as ws
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.const import CONF_COORDINATES
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clock(freezer, monkeypatch):
    freezer.move_to(NOW)
    monkeypatch.setattr(ws, "DEBOUNCE", 0.01)
    monkeypatch.setattr(ws, "RETRY_DELAYS", (0.01, 0.01))


@pytest.fixture
async def local(hass, entry, app_client, hass_ws_client):
    return await setup_local(hass, entry, app_client, hass_ws_client)


@pytest.fixture
async def replay_local(hass, entry, app_client, hass_ws_client):
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_COORDINATES: True})
    return await setup_local(hass, entry, app_client, hass_ws_client)


async def setup_local(hass, entry, app_client, hass_ws_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    app_client.reset_mock()
    device = next(
        row.id
        for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in row.identifiers
    )
    try:
        client = await hass_ws_client(hass)
    except TypeError:
        client = await hass_ws_client()
    return device, client, entry.runtime_data.coordinator


async def subscribe(client, device, identity=1, **options):
    await client.send_json(
        {"id": identity, "type": "ninebot/history/subscribe", "device_ids": [device], **options}
    )
    return await asyncio.wait_for(client.receive_json(), 5)


async def advance(hass, freezer):
    """Deliver dispatcher callbacks, then advance HA's frozen monotonic timers."""
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=1))
    await hass.async_block_till_done()


async def test_real_commit_is_debounced_and_unsubscribe_is_local(
    hass, entry, app_client, local, freezer
):
    device, client, co = local
    assert (await subscribe(client, device))["success"]
    baseline = (await client.receive_json())["event"]
    assert set(baseline) == {"schema_version", "type", "vehicles"}
    assert baseline["type"] == "baseline"
    assert set(baseline["vehicles"][0]) == {"device_id", "selection_version"}
    at = int((NOW - timedelta(minutes=10)).timestamp())
    await co.statistics.async_record(
        "SyntheticSN",
        travel(
            {
                "times": 1,
                "list": [
                    {
                        "travel_id": "synthetic-ride",
                        "start_time": at,
                        "end_time": at + 300,
                        "mileages": 1,
                        "duration": 300,
                    }
                ],
            },
            "202610",
        ),
        NOW + timedelta(seconds=1),
    )
    for _ in range(3):
        co.statistics._notify_change("SyntheticSN", None)
    await advance(hass, freezer)
    event = (await asyncio.wait_for(client.receive_json(), 5))["event"]
    assert event["type"] == "changed"
    assert event["vehicles"][0]["selection_version"] != baseline["vehicles"][0]["selection_version"]
    await client.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
    assert not ws.feeds(hass)
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_permissions_before_subscription_and_again_before_delivery(hass, local, freezer):
    device, client, co = local
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    user.permissions.check_entity.return_value = False
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        rejected = await subscribe(client, device)
        assert rejected["error"]["code"] == "unauthorized" and not ws.feeds(hass)
        user.permissions.access_all_entities.return_value = True
        assert (await subscribe(client, device, 2))["success"]
        await client.receive_json()
        user.is_active = False
        co.statistics._notify_change("SyntheticSN", None)
        await advance(hass, freezer)
        closed = (await client.receive_json())["event"]
        assert closed == {"schema_version": 1, "type": "closed", "reason": "denied"}
        assert not ws.feeds(hass)


async def test_permission_is_rechecked_after_version_read(hass, local, monkeypatch):
    device, client, co = local
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = True
    original = co.statistics.archive.async_catalog_version

    async def revoked(sn):
        version = await original(sn)
        user.is_active = False
        return version

    monkeypatch.setattr(co.statistics.archive, "async_catalog_version", revoked)
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        assert (await subscribe(client, device))["error"]["code"] == "unauthorized"
    assert not ws.feeds(hass)


async def test_unload_closes_and_releases_entry_and_connection_callbacks(hass, entry, local):
    device, client, _ = local
    assert (await subscribe(client, device))["success"]
    await client.receive_json()
    feed = next(iter(ws.feeds(hass)))
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert (await client.receive_json())["event"]["type"] == "closed"
    assert feed.closed and not feed.scopes and not feed.listeners and feed.task is None
    assert not ws.feeds(hass)


async def test_socket_close_cancels_pending_authorization_read(hass, local, monkeypatch):
    device, client, co = local
    started = asyncio.Event()

    async def pending(sn):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(co.statistics.archive, "async_catalog_version", pending)
    await client.send_json({"id": 1, "type": "ninebot/history/subscribe", "device_ids": [device]})
    await started.wait()
    await client.close()
    await hass.async_block_till_done()
    assert not ws.feeds(hass)


async def test_archive_busy_retries_without_closing_after_one_failure(
    hass, local, monkeypatch, freezer
):
    device, client, co = local
    assert (await subscribe(client, device))["success"]
    await client.receive_json()
    original = await co.statistics.archive.async_catalog_version("SyntheticSN")
    query = AsyncMock(side_effect=[ArchiveError(ArchiveFailure.BUSY), original])
    monkeypatch.setattr(co.statistics.archive, "async_catalog_version", query)
    co.statistics._notify_change("SyntheticSN", None)
    await advance(hass, freezer)
    await advance(hass, freezer)
    assert (await client.receive_json())["event"]["type"] == "changed"
    assert query.await_count == 2
    assert len(ws.feeds(hass)) == 1


async def test_distinct_devices_and_subscription_capacity_are_bounded(hass, local, monkeypatch):
    device, client, _ = local
    await client.send_json(
        {"id": 1, "type": "ninebot/history/subscribe", "device_ids": [device, device]}
    )
    assert (await client.receive_json())["error"]["code"] == "invalid_format"
    monkeypatch.setattr(ws, "MAX_CONNECTION", 1)
    assert (await subscribe(client, device, 2))["success"]
    await client.receive_json()
    assert (await subscribe(client, device, 3))["error"]["code"] == "invalid_format"
    assert len(ws.feeds(hass)) == 1


@pytest.mark.parametrize("notify", [True, False])
async def test_replay_access_revocation_checks_quiet_archive_without_reading_it(
    hass, replay_local, monkeypatch, freezer, notify
):
    device, client, co = replay_local
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    tracker_allowed = True
    user.permissions.check_entity.side_effect = lambda entity, _: (
        entity.startswith("calendar.") or (tracker_allowed and entity.startswith("device_tracker."))
    )
    catalog = AsyncMock(wraps=co.statistics.archive.async_catalog_version)
    monkeypatch.setattr(co.statistics.archive, "async_catalog_version", catalog)
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        assert (await subscribe(client, device, include_track=True))["success"]
        assert (await client.receive_json())["event"]["type"] == "baseline"
        feed = next(iter(ws.feeds(hass)))
        if not notify:
            freezer.tick(timedelta(seconds=ws.ACCESS_INTERVAL + 0.1))
            await hass.async_block_till_done()
            assert not feed.closed and feed.access_timer is not None and feed.access_task is None
        tracker_allowed = False
        if notify:
            hass.bus.async_fire(ws.EVENT_USER_UPDATED, {"user_id": feed.context.user_id})
            await hass.async_block_till_done()
        else:
            freezer.tick(timedelta(seconds=ws.ACCESS_INTERVAL + 0.1))
            await hass.async_block_till_done()
        closed = (await asyncio.wait_for(client.receive_json(), 5))["event"]
        assert closed == {"schema_version": 1, "type": "closed", "reason": "denied"}
        assert feed.closed and feed.access_timer is None and feed.access_task is None
        catalog.assert_awaited_once_with("SyntheticSN")
        assert (await subscribe(client, device, 2, include_track=True))["error"][
            "code"
        ] == "unauthorized"
        assert (await subscribe(client, device, 3))["success"]


async def test_coordinate_option_change_retires_replay_and_hides_vehicle(hass, entry, replay_local):
    device, client, _ = replay_local
    await client.send_json({"id": 1, "type": "ninebot/history/vehicles"})
    result = (await client.receive_json())["result"]
    assert result["more_available"] is False
    (row,) = result["vehicles"]
    assert row == {"account_id": entry.entry_id, "device_id": device, "label": row["label"]}
    assert not {"fake-account", "SyntheticSN", "synthetic-business"}.intersection(
        row["label"].split()
    )
    assert (await subscribe(client, device, 2, include_track=True))["success"]
    await client.receive_json()
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_COORDINATES: False}
    )
    closed = (await asyncio.wait_for(client.receive_json(), 5))["event"]
    assert closed == {"schema_version": 1, "type": "closed", "reason": "denied"}
    await hass.async_block_till_done()
    await client.send_json({"id": 3, "type": "ninebot/history/vehicles"})
    assert (await client.receive_json())["result"] == {"vehicles": [], "more_available": False}


async def test_favorite_reopens_exact_local_ride_with_current_access(
    hass, entry, replay_local, app_client
):
    device, client, co = replay_local
    at = int((NOW - timedelta(minutes=10)).timestamp())
    await co.statistics.async_record(
        "SyntheticSN",
        travel(
            {
                "times": 1,
                "list": [
                    {
                        "travel_id": "saved-favorite",
                        "start_time": at,
                        "end_time": at + 300,
                        "mileages": 1,
                        "duration": 300,
                        "ec": 0,
                    }
                ],
            },
            "202610",
        ),
        NOW + timedelta(seconds=1),
    )
    request = {
        "type": "ninebot/history/ride_summary",
        "account_id": entry.entry_id,
        "device_id": device,
        "query_month": "202610",
        "ride_id": "saved-favorite",
    }

    async def read(identity, **overrides):
        await client.send_json({**request, **overrides, "id": identity})
        return await asyncio.wait_for(client.receive_json(), 5)

    row = (await read(1))["result"]["ride"]
    assert row["device_id"] == device and row["energy_wh"] == 0
    assert row["detail_saved"] is False and "track" not in row
    assert (await read(2, query_month="202609"))["result"]["ride"] is None
    assert (await read(3, account_id="other-account"))["error"]["code"] == "unauthorized"
    with patch.object(ws, "async_check_history_access", side_effect=ws.Unauthorized):
        assert (await read(4))["error"]["code"] == "unauthorized"
    assert not app_client.mock_calls
