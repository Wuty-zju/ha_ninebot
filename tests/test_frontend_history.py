"""Packaged static routes, authenticated responses and legacy Action permissions."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import async_setup
from custom_components.ninebot.frontend import CARD_URL, async_register_frontend
from custom_components.ninebot.services import async_scoped_action

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def ui_clock(freezer):
    freezer.move_to(NOW)


@pytest.fixture
async def device(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    app_client.reset_mock()
    return next(
        row.id
        for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in row.identifiers
    )


async def test_package_http_routes_and_registration_idempotence(hass, device, hass_client):
    client = await hass_client()
    for url, filename in (
        (CARD_URL, "ninebot-trip-card.js"),
        ("/ninebot-static/labels.js", "labels.js"),
    ):
        response = await client.get(url)
        assert response.status == 200
        assert (await response.text()) == (
            Path(__file__).parents[1] / "custom_components/ninebot/frontend" / filename
        ).read_text()
        assert "max-age=2678400" not in response.headers.get("Cache-Control", "")
    assert await async_setup(hass, {})  # No duplicate route on component re-entry.
    assert (await client.get(CARD_URL)).status == 200
    assert (await client.get("/ninebot-static/archive.sqlite3")).status == 404
    assert (await client.get("/ninebot-static/session.json")).status == 404
    assert (await client.get("/ninebot-static/")).status in (403, 404)


async def test_direct_setup_without_http_and_static_failure_is_retryable(hass):
    await async_register_frontend(hass)
    assert not hass.data.get("ninebot_frontend")
    http = SimpleNamespace(async_register_static_paths=AsyncMock(side_effect=OSError))
    hass.http = http
    with pytest.raises(OSError):
        await async_register_frontend(hass)
    assert not hass.data.get("ninebot_frontend")
    http.async_register_static_paths.side_effect = None
    await async_register_frontend(hass)
    assert hass.data["ninebot_frontend"] is True


async def test_standard_authenticated_ws_response_is_local_and_bounded(
    hass, device, app_client, hass_ws_client
):
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "call_service",
            "domain": "ninebot",
            "service": "get_recorded_trips",
            "service_data": {
                "device_id": device,
                "start_date": "2026-10-01",
                "end_date": "2026-10-08",
            },
            "return_response": True,
        }
    )
    result = await client.receive_json()
    assert result["success"]
    data = result["result"]["response"]
    assert data["source_mode"] == "ride_archive" and data["rides"] == []
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


@pytest.mark.parametrize(
    "service,fields",
    [
        ("get_history", {"start_month": "202610", "end_month": "202610"}),
        ("get_statistics", {"start_month": "202610", "end_month": "202610"}),
        ("get_entity_migration", {}),
        ("sync_history", {"operation": "status"}),
        ("sync_history", {"operation": "start", "start_month": "202601", "end_month": "202602"}),
        ("import_statistics", {"start_month": "202609", "end_month": "202609"}),
    ],
)
async def test_legacy_actions_share_read_or_control_scope_before_any_work(
    hass, device, app_client, service, fields
):
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    user.permissions.check_entity.return_value = False
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                "ninebot",
                service,
                {"device_id": device, **fields},
                blocking=True,
                return_response=True,
                context=Context(user_id="synthetic-restricted"),
            )
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_read_scope_cannot_mutate_sync_or_recorder_and_rechecks_on_return(
    hass, entry, device
):
    calendar = next(
        row.entity_id
        for row in er.async_entries_for_device(er.async_get(hass), device)
        if row.domain == "calendar"
    )
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    user.permissions.check_entity.side_effect = lambda entity, permission: (
        entity == calendar and permission == "read"
    )
    context = Context(user_id="synthetic-reader")
    handler = AsyncMock(return_value={"safe": True})
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        for operation in ("start", "continue", "cancel"):
            call = ServiceCall(
                hass,
                "ninebot",
                "sync_history",
                {"device_id": device, "operation": operation},
                context=context,
            )
            with pytest.raises(Unauthorized):
                await async_scoped_action(hass, handler, call)
        call = ServiceCall(
            hass,
            "ninebot",
            "sync_history",
            {"device_id": device, "operation": "status"},
            context=context,
        )
        assert await async_scoped_action(hass, handler, call) == {"safe": True}
    with patch.object(hass.auth, "async_get_user", AsyncMock(side_effect=[user, None])):
        with pytest.raises(Unauthorized):
            await async_scoped_action(hass, handler, call)
    co = entry.runtime_data.coordinator

    async def invalidate(*args):
        co._generation += 1
        return {"safe": True}

    with pytest.raises(HomeAssistantError) as error:
        await async_scoped_action(
            hass,
            invalidate,
            ServiceCall(hass, "ninebot", "get_entity_migration", {"device_id": device}),
        )
    assert error.value.translation_key == "query_unavailable"
