"""Real authenticated HA WebSocket contract, synthetic local archive only."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.ninebot import history_websocket as ws
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clock(freezer, monkeypatch):
    freezer.move_to(NOW)
    monkeypatch.setattr(ws, "DEBOUNCE", 0)
    monkeypatch.setattr(ws, "RETRY_DELAYS", (0, 0))


@pytest.fixture
async def local(hass, entry, app_client, hass_ws_client):
    print("WS fixture: setup synthetic entry", flush=True)
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
    print("WS fixture: authenticated client ready", flush=True)
    return device, client, entry.runtime_data.coordinator


async def subscribe(client, device, identity=1):
    await client.send_json(
        {"id": identity, "type": "ninebot/history/subscribe", "device_ids": [device]}
    )
    print("WS awaiting subscription result", identity, flush=True)
    return await asyncio.wait_for(client.receive_json(), 5)


async def test_real_commit_is_debounced_and_unsubscribe_is_local(hass, entry, app_client, local):
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
    print("WS awaiting changed after archive commit", flush=True)
    event = (await asyncio.wait_for(client.receive_json(), 5))["event"]
    assert event["type"] == "changed"
    assert event["vehicles"][0]["selection_version"] != baseline["vehicles"][0]["selection_version"]
    await client.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
    assert not ws.feeds(hass)
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_permissions_before_subscription_and_again_before_delivery(hass, local):
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


async def test_archive_busy_retries_without_closing_after_one_failure(hass, local, monkeypatch):
    device, client, co = local
    assert (await subscribe(client, device))["success"]
    await client.receive_json()
    original = await co.statistics.archive.async_catalog_version("SyntheticSN")
    query = AsyncMock(side_effect=[ArchiveError(ArchiveFailure.BUSY), original])
    monkeypatch.setattr(co.statistics.archive, "async_catalog_version", query)
    co.statistics._notify_change("SyntheticSN", None)
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
