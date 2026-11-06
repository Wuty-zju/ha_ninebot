"""Synthetic native-tool query contracts against real HA Actions and local SQLite."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr

from custom_components.ninebot.assistant_queries import SPECS, async_query, period_dates
from custom_components.ninebot.assistant_views import AssistantView, numeric, statistics_view
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.travel import merge_detail, parse_ride

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
SN = "SyntheticSN"


@pytest.fixture
async def assistant(hass, entry, app_client, freezer):
    freezer.move_to(NOW)
    start = NOW - timedelta(days=1)
    app_client.async_get_travel.return_value = {
        "total_mileages": 2.4,
        "ec": 240,
        "times": 2,
        "duration": 1200,
        "detail": [0] * 5 + [2.4] + [0] * 25,
        "list": [
            {
                "travel_id": f"ride-{index}",
                "mileages": 1.2,
                "ec": 120,
                "duration": 600,
                "speed": 30,
                "start_time": int(start.timestamp()) + index * 1000,
                "end_time": int(start.timestamp()) + index * 1000 + 600,
            }
            for index in range(2)
        ],
    }
    principal = SimpleNamespace(
        is_active=True,
        is_admin=True,
        permissions=SimpleNamespace(access_all_entities=Mock(return_value=True)),
    )
    with (
        patch("custom_components.ninebot.async_update_assistant_api", new=AsyncMock()),
        patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"),
        patch.object(hass.auth, "async_get_user", AsyncMock(return_value=principal)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        co = entry.runtime_data.coordinator
        co.realtime.stop()
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                "enable_assistant": True,
                "assistant_vehicles": [SN],
            },
        )
        await hass.async_block_till_done()
        device = next(
            device.id
            for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if ("ninebot", SN) in device.identifiers
        )
        app_client.reset_mock()
        yield SimpleNamespace(
            device=device,
            context=Context(user_id="synthetic-admin"),
            co=co,
            principal=principal,
            entry=entry,
        )
        for method in (
            app_client.async_list_vehicles,
            app_client.async_get_status,
            app_client.async_get_battery,
            app_client.async_get_travel,
            app_client.async_get_trip_detail,
            app_client.async_control,
        ):
            method.assert_not_awaited()


async def query(hass, assistant, key, **arguments):
    return await async_query(
        hass, key, {"vehicle_ref": assistant.device, **arguments}, assistant.context
    )


@pytest.mark.parametrize(
    "key,arguments",
    [
        ("get_vehicle_status", {"refresh": True}),
        ("get_vehicle_status", {"fields": ["latitude"]}),
        ("get_statistics", {"period": "all"}),
        ("get_statistics", {"period": "today", "sql": "SELECT *"}),
        ("get_trip_detail", {"ride_id": "ride-0", "query_month": "202610", "include_track": True}),
        (
            "get_recorded_trips",
            {"start_date": "2026-10-01", "end_date": "2026-10-07", "limit": True},
        ),
        ("get_recorded_trips", {"start_date": "2026-10-01", "end_date": "2026-10-07", "limit": 21}),
        ("get_recorded_trips", {"start_date": "2026-10-01", "end_date": "2026-11-30"}),
        ("get_recorded_trips", {"start_date": "2026-10-07", "end_date": "2026-10-01"}),
        ("engine_start", {}),
    ],
)
async def test_strict_arguments_do_not_reach_actions(hass, assistant, key, arguments):
    with patch.object(type(hass.services), "async_call", new=AsyncMock()) as action:
        result, error = await query(hass, assistant, key, **arguments)
    assert error and result["error"]["code"] == "invalid_arguments"
    action.assert_not_awaited()


async def test_anonymous_or_revoked_user_is_not_an_automation(hass, assistant):
    for context in (None, Context()):
        result, error = await async_query(hass, "get_vehicles", {}, context)
        assert error and result["error"]["code"] == "not_authorized"
    assistant.principal.is_active = False
    result, error = await async_query(hass, "get_vehicles", {}, assistant.context)
    assert error and result["error"]["code"] == "not_authorized"


async def test_cached_status_keeps_zero_false_stale_and_excludes_private_fields(hass, assistant):
    snapshot = assistant.co.data[SN]
    assistant.co.data[SN] = replace(
        snapshot,
        status=replace(
            snapshot.status,
            battery=0,
            charging=False,
            latitude=31.234567,
            longitude=120.123456,
            observations={"acc_raw": 1, "seat_lock_raw": 1, "private": "never-forward"},
        ),
        battery=replace(
            snapshot.battery,
            batteries=tuple(
                replace(pack, key="private-pack-serial") for pack in snapshot.battery.batteries
            ),
        ),
    )
    result, error = await query(hass, assistant, "get_vehicle_status")
    assert not error
    assert result["data"]["battery_soc_percent"] == 0
    assert result["data"]["charging"] is False
    assert result["data"]["acc_active"] is True and result["data"]["seat_locked"] is False
    assert result["quality"]["metrics"]["battery_soc_percent"]["status"] == "available"
    encoded = json.dumps(result)
    assert not any(
        value in encoded
        for value in (
            SN,
            "fake-account",
            "latitude",
            "longitude",
            "never-forward",
            "private-pack-serial",
        )
    )
    with patch.object(assistant.co, "fresh", return_value=False):
        stale, error = await query(hass, assistant, "get_vehicle_status")
    assert not error and stale["data"]["battery_soc_percent"] == 0
    assert stale["quality"]["metrics"]["battery_soc_percent"]["status"] == "stale"
    assert stale["quality"]["groups"]["status"]["received_at"] == NOW.isoformat()


async def test_vehicle_list_uses_consented_names_and_explicit_fleet_limit(hass, assistant):
    snapshot = assistant.co.data[SN]
    assistant.co.data[SN] = replace(
        snapshot,
        profile=replace(
            snapshot.profile,
            name=(f"Scooter {SN} fake-account 12345678900 synthetic-business " + "a" * 32),
        ),
    )
    result, error = await async_query(hass, "get_vehicles", {}, assistant.context)
    assert not error and result["data"]["vehicles"][0]["label"].strip() == "Scooter"
    assert result["data"]["more_available"] is False
    names = [SN]
    for index in range(20):
        name = f"fleet-{index:02}"
        names.append(name)
        assistant.co.data[name] = replace(snapshot, profile=replace(snapshot.profile, sn=name))
        dr.async_get(hass).async_get_or_create(
            config_entry_id=assistant.entry.entry_id, identifiers={("ninebot", name)}, name="Fleet"
        )
    hass.config_entries.async_update_entry(
        assistant.entry,
        options={
            **assistant.entry.options,
            "assistant_vehicles": names,
        },
    )
    result, error = await async_query(hass, "get_vehicles", {}, assistant.context)
    assert not error and len(result["data"]["vehicles"]) == 20
    assert result["data"]["more_available"] is True


@pytest.mark.parametrize(
    "period,count,month",
    [
        ("today", 0, "202610"),
        ("yesterday", 2, "202610"),
        ("this_month", 2, "202610"),
        ("last_month", None, "202609"),
    ],
)
async def test_period_statistics_reuse_actions_with_original_context_and_no_refresh(
    hass, assistant, period, count, month
):
    original = hass.services.async_call
    with patch.object(type(hass.services), "async_call", new=AsyncMock(wraps=original)) as action:
        result, error = await query(hass, assistant, "get_statistics", period=period)
    assert not error and result["data"]["ride_count"] == count
    assert result["data"]["units"]["duration_s"] == "s"
    assert action.await_args.kwargs["context"] is assistant.context
    assert action.await_args.args[2]["start_month"] == month
    assert action.await_args.args[2]["refresh"] is False
    if count is None:
        assert result["quality"]["metrics"]["ride_count"]["status"] == "missing"
    if period in {"today", "yesterday"}:
        assert result["evidence"]["ride_date_assignment"] == "whole_ride_end_business_date"
        assert "server_daily_distance_and_ride_end_date_can_differ" in result["warnings"]
    else:
        assert result["evidence"]["ride_date_assignment"] == "server_month"


async def test_recorded_pages_are_local_bounded_and_cursor_replay_is_explicit(hass, assistant):
    first, error = await query(
        hass,
        assistant,
        "get_recorded_trips",
        start_date="2026-10-06",
        end_date="2026-10-06",
        limit=1,
    )
    assert not error and first["data"]["returned"] == 1 and first["data"]["has_more"]
    assert first["data"]["rides"][0]["ride_id"] == "ride-1"
    assert "returned_page_is_not_a_period_total" in first["warnings"]
    second, error = await query(
        hass,
        assistant,
        "get_recorded_trips",
        start_date="2026-10-06",
        end_date="2026-10-06",
        limit=1,
        cursor=first["data"]["next_cursor"],
    )
    assert not error and second["data"]["rides"][0]["ride_id"] == "ride-0"
    replay, error = await query(
        hass,
        assistant,
        "get_recorded_trips",
        start_date="2026-10-06",
        end_date="2026-10-06",
        limit=1,
        cursor=first["data"]["next_cursor"],
    )
    assert error and replay["error"]["code"] == "retry_later"
    assert next(spec for spec in SPECS if spec.key == "get_recorded_trips").idempotent is False


async def test_detail_missing_never_falls_back_to_cloud_then_saved_detail_is_small(
    hass, assistant, freezer
):
    missing, error = await query(
        hass, assistant, "get_trip_detail", ride_id="ride-0", query_month="202610"
    )
    assert error and missing["error"]["code"] == "local_data_missing"
    summary = next(ride for ride in assistant.co.data[SN].travel.rides if ride.ride_id == "ride-0")
    raw = {
        "mileages": 1.2,
        "duration": 600,
        "ec": 120,
        "speed": 30,
        "trail": "120,30,1,1;120.001,30.001,2,2",
        "is_show_simple_point": False,
    }
    received = NOW + timedelta(seconds=1)
    freezer.move_to(received)
    evidence = build_record(Endpoint.TRIP_DETAIL, raw, received, "202610", backend_version="0.1.7")
    ride = merge_detail(summary, parse_ride(raw, "202610", source=Endpoint.TRIP_DETAIL))
    assert await assistant.co.statistics.archive.async_record_detail(
        SN, ride, received, raw=evidence
    )
    result, error = await query(
        hass, assistant, "get_trip_detail", ride_id="ride-0", query_month="202610"
    )
    assert not error, result
    assert result["data"]["detail_saved"] is True
    assert result["data"]["distance_m"] == 1200 and result["data"]["energy_wh"] == 120
    assert result["quality"]["valid_points"] == 2
    encoded = json.dumps(result)
    assert not {"speed_samples", "distance_samples", "track"} & result["data"].keys()
    assert not any(value in encoded for value in ("latitude", "trail", "120.001"))


async def test_sync_health_is_read_only_and_reports_the_local_state(hass, assistant):
    result, error = await query(hass, assistant, "get_sync_health")
    assert not error
    assert result["data"]["background_enabled"] is False
    assert result["evidence"]["operation"] == "sync_status"
    assert result["quality"]["upstream_history_complete"] == "unverified"


async def test_revocation_while_action_is_awaited_discards_the_result(hass, assistant):
    async def revoked(*args, **kwargs):
        assert kwargs["context"] is assistant.context
        hass.config_entries.async_update_entry(
            assistant.entry,
            options={
                **assistant.entry.options,
                "enable_assistant": False,
            },
        )
        return {"months": [{"month": "202610", "distance_km": 12345}]}

    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=revoked)):
        result, error = await query(hass, assistant, "get_statistics", period="this_month")
    assert error and result["error"]["code"] == "not_authorized"
    assert "12345" not in json.dumps(result)


async def test_response_limit_counts_utf8_bytes_and_never_returns_a_fragment(hass, assistant):
    with patch(
        "custom_components.ninebot.assistant_queries.status_view",
        return_value=AssistantView({"large": "测" * 6000}),
    ):
        result, error = await query(hass, assistant, "get_vehicle_status")
    assert error and result["error"]["code"] == "response_too_large"
    assert "data" not in result


def test_projection_quality_closed_periods_and_invalid_values():
    old = {
        "distance_km": 0,
        "ride_count": None,
        "duration_s": float("nan"),
        "energy_wh": True,
        "period_closed": True,
        "observed_after_period_end": True,
        "received_at": "2026-09-01T00:00:00+00:00",
    }
    view = statistics_view(old, daily=False, now=NOW)
    assert view.data["distance_km"] == 0
    assert view.quality["metrics"]["distance_km"]["status"] == "available"
    assert view.data["duration_s"] is view.data["energy_wh"] is None
    assert (
        statistics_view({**old, "observed_after_period_end": False}, daily=False, now=NOW).quality[
            "metrics"
        ]["distance_km"]["status"]
        == "partial"
    )
    assert numeric(10**1000) is None


def test_business_period_uses_one_business_clock_at_month_rollover():
    now = datetime(2026, 10, 31, 16, 0, 1, tzinfo=UTC)
    assert [day.isoformat() for day in period_dates("today", now)] == ["2026-11-01"] * 2
    assert [day.isoformat() for day in period_dates("last_month", now)] == [
        "2026-10-01",
        "2026-10-31",
    ]
