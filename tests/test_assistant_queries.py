"""Synthetic native-tool query contracts against real HA Actions and local SQLite."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.assistant_errors import error_data
from custom_components.ninebot.assistant_knowledge import READ_TOOLS, KnowledgeUnavailable
from custom_components.ninebot.assistant_limits import DATA_KEY as LIMITS_KEY
from custom_components.ninebot.assistant_policy import AssistantError, async_require_admin
from custom_components.ninebot.assistant_queries import SPECS, async_query, period_dates
from custom_components.ninebot.assistant_views import (
    AssistantView,
    detail_view,
    numeric,
    statistics_view,
    trips_view,
)
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.travel import merge_detail, parse_ride

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
SN = "SyntheticSN"


@pytest.fixture
async def assistant(hass, entry, app_client, freezer, request):
    freezer.move_to(NOW)
    if (budget := getattr(request, "param", None)) is not None:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "history_requests_per_hour": budget}
        )
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
        ("get_statistics", {"period": "2026-02-30"}),
        ("get_statistics", {"period": "2026-2-01"}),
        ("get_statistics", {"period": "20261006"}),
        ("get_statistics", {"period": "1999-12-31"}),
        ("get_statistics", {"period": "2026-10-08"}),
        ("get_statistics", {"period": "2026-11"}),
        ("get_statistics", {"period": "2026-13"}),
        ("get_statistics", {"period": "2026-9"}),
        ("get_statistics", {"period": "1999-12"}),
        ("explain_statistics", {"period": "today"}),
        ("explain_statistics", {"period": "today", "metric": "latitude"}),
        ("get_statistics", {"period": "2026-10-06", "end_date": "2026-10-07"}),
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
    assert result["evidence"]["capabilities_basis"] == "local_cache_and_archive_readiness"
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
        ("2026-10-06", 2, "202610"),
        ("2026-10-05", 0, "202610"),
        ("2026-09-30", None, "202609"),
        ("2026-10", 2, "202610"),
        ("2026-09", None, "202609"),
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
    if period[0].isdigit() and len(period) == 10:
        assert result["query"]["period"] == period
        assert result["query"]["start_date"] == result["query"]["end_date"] == period
        assert action.await_args.args[2]["include_daily"] is True
    if len(period) == 7:
        assert result["query"]["period"] == period
        assert result["query"]["start_date"] == f"{period}-01"
        assert action.await_args.args[2]["include_daily"] is False
    if count is None:
        assert result["quality"]["metrics"]["ride_count"]["status"] == "missing"
    if period in {"today", "yesterday", "2026-10-06", "2026-10-05", "2026-09-30"}:
        assert result["evidence"]["ride_date_assignment"] == "whole_ride_end_business_date"
        assert "server_daily_distance_and_ride_end_date_can_differ" in result["warnings"]
    else:
        assert result["evidence"]["ride_date_assignment"] == "server_month"


async def test_explicit_business_date_preserves_incomplete_daily_metrics(hass, assistant):
    payload = {
        "total_mileages": 14.8,
        "ec": 340,
        "times": 2,
        "duration": 2671,
        "detail": [0] * 5 + [14.8] + [0] * 24,
        "list": [
            {
                "travel_id": "partial-ride",
                "mileages": 14.8,
                "ec": 340,
                "start_time_format": "2026-09-06 19:00:00",
                "end_time_format": "2026-09-06 19:44:31",
                "duration": 2671,
            }
        ],
    }
    await assistant.co.statistics.async_record(SN, travel(payload, "202609"), NOW)
    result, error = await query(hass, assistant, "get_statistics", period="2026-09-06")
    assert not error and result["data"]["distance_km"] == 14.8
    assert result["data"]["ride_count"] is result["data"]["energy_wh"] is None
    assert result["quality"]["metrics"]["ride_count"] == {
        "status": "missing",
        "reason": "incomplete_month_list",
    }
    assert result["quality"]["ride_window_complete"] is False
    assert result["evidence"]["distance_basis"] == "server_daily_chart"


async def test_recorded_pages_are_local_bounded_and_cursor_replay_is_explicit(
    hass, assistant, freezer
):
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
    assert first["data"]["rides"][0]["received_at"] == NOW.isoformat()
    assert first["quality"]["page_as_of"] == NOW.isoformat()
    assert "returned_page_is_not_a_period_total" in first["warnings"]
    later = NOW + timedelta(minutes=5)
    freezer.move_to(later)
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
    assert second["query"]["as_of"] == later.isoformat()
    assert second["quality"]["page_as_of"] == first["quality"]["page_as_of"]
    assert second["data"]["rides"][0]["received_at"] == NOW.isoformat()
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


async def test_recorded_empty_page_preserves_known_month_observation(hass, assistant, freezer):
    later = NOW + timedelta(days=1)
    freezer.move_to(later)
    result, error = await query(
        hass,
        assistant,
        "get_recorded_trips",
        start_date="2026-10-05",
        end_date="2026-10-05",
    )
    assert not error and result["data"]["rides"] == []
    assert result["query"]["as_of"] == result["quality"]["page_as_of"] == later.isoformat()
    assert result["quality"]["months"] == [
        {"month": "202610", "received_at": NOW.isoformat(), "observed_list_complete": True}
    ]
    assert result["quality"]["all_observed_lists_complete"] is True


async def test_recorded_ride_limit_counts_the_returned_observation_timestamp(hass, assistant):
    arguments = {"start_date": "2026-10-06", "end_date": "2026-10-06", "limit": 1}
    result, error = await query(hass, assistant, "get_recorded_trips", **arguments)
    assert not error
    ride = result["data"]["rides"][0]
    assert ride["received_at"] == NOW.isoformat()
    without_observation = {key: value for key, value in ride.items() if key != "received_at"}
    old_size = len(json.dumps(without_observation, ensure_ascii=False).encode())
    returned_size = len(json.dumps(ride, ensure_ascii=False).encode())
    limit = (old_size + returned_size) // 2
    assert old_size < limit < returned_size
    with patch("custom_components.ninebot.assistant_queries.MAX_RIDE_BYTES", limit):
        rejected, error = await query(hass, assistant, "get_recorded_trips", **arguments)
    assert error and rejected["error"]["code"] == "response_too_large"
    assert "data" not in rejected


def test_recorded_observation_projection_is_bounded_and_whitelisted():
    old = "2020-01-01T00:00:00+00:00"
    response = {
        "range": {"as_of": NOW, "private": "never-forward"},
        "months": [
            {
                "month": month,
                "received_at": old,
                "observed_list_complete": True,
                "raw": "never-forward",
            }
            for month in ("202001", "202002", "202003", "202004")
        ],
        "private": "never-forward",
    }
    rides = [{"ride_id": "ride-0", "received_at": old, "private": "never-forward"}]
    view = trips_view(response, rides)
    assert view.data["rides"][0]["received_at"] == old
    assert view.quality["page_as_of"] == NOW.isoformat()
    assert view.quality["months"] == [
        {"month": month, "received_at": old, "observed_list_complete": True}
        for month in ("202001", "202002", "202003")
    ]
    assert "never-forward" not in json.dumps({"data": view.data, "quality": view.quality})
    assert "received_at" not in detail_view({"ride": rides[0]}).data


@pytest.mark.parametrize("metadata", [None, [], "invalid", {}])
def test_recorded_missing_observation_metadata_stays_unknown(metadata):
    view = trips_view({"range": metadata, "months": metadata}, [{}])
    assert view.data["rides"][0]["received_at"] is None
    assert view.quality["page_as_of"] is None
    assert view.quality["months"] == []


@pytest.mark.parametrize("invalid_time", [None, "invalid", "2026-10-07T04:00:00", True, {}])
def test_recorded_invalid_observation_metadata_is_not_fabricated(invalid_time):
    view = trips_view(
        {
            "range": {"as_of": invalid_time},
            "months": [
                {
                    "month": "202610",
                    "received_at": invalid_time,
                    "observed_list_complete": "true",
                },
                {"month": "202613", "received_at": NOW},
                {"month": 202610, "received_at": NOW},
            ],
        },
        [{"received_at": invalid_time}],
    )
    assert view.data["rides"][0]["received_at"] is None
    assert view.quality["page_as_of"] is None
    assert view.quality["months"] == [
        {"month": "202610", "received_at": None, "observed_list_complete": None}
    ]


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


@pytest.mark.parametrize(
    "assistant,budget,interval", [(0, 0, 0), (30, 30, 120), (60, 60, 60)], indirect=["assistant"]
)
async def test_sync_health_reports_configured_pacing_not_invented_remaining_calls(
    hass, assistant, budget, interval
):
    result, error = await query(hass, assistant, "get_sync_health")
    assert not error, result
    assert result["data"]["request_policy"] == {
        "configured_requests_per_hour": budget,
        "minimum_interval_s": interval,
        "basis": "paced_account_requests",
    }
    assert "remaining" not in result["data"]


@pytest.mark.parametrize(
    "option", [{}, {"include_procedures": False}, {"include_procedures": True}]
)
async def test_help_reads_only_bundled_sources_and_never_calls_actions(hass, assistant, option):
    with (
        patch.object(type(hass.services), "async_call", new=AsyncMock()) as action,
        patch(
            "custom_components.ninebot.assistant_queries.async_require_admin",
            new=AsyncMock(wraps=async_require_admin),
        ) as require_admin,
    ):
        result, error = await async_query(
            hass,
            "search_help",
            {"query": "等待请求额度", "language": "zh-Hans", **option},
            assistant.context,
        )
    assert not error
    assert "vehicle_ref" not in result
    assert result["data"]["matches"][0]["source_id"] == "sync_budget"
    assert result["evidence"]["source_kind"] == "bundled_public_help"
    assert result["data"]["matches"][0]["procedure_id"] == "SK06"
    assert "citation" in result["data"]["matches"][0]
    if option.get("include_procedures", False):
        assert result["data"]["procedures"][0]["id"] == "SK06"
        assert set(result["data"]["procedures"][0]["allowed_tools"]) <= {
            f"ninebot__{spec.key}" for spec in SPECS
        }
    else:
        assert result["data"]["procedures"] == []
    assert all(name in {spec.key for spec in SPECS} for name in READ_TOOLS)
    assert len(json.dumps(result["data"], ensure_ascii=False).encode()) <= 4096
    assert require_admin.call_count > 0
    assert all(call.args[1] is assistant.context for call in require_admin.call_args_list)
    action.assert_not_awaited()


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
async def test_help_rejects_non_boolean_procedure_option_before_read(hass, assistant, value):
    with (
        patch("custom_components.ninebot.assistant_queries.search_help") as search,
        patch.object(type(hass.services), "async_call", new=AsyncMock()) as action,
    ):
        result, error = await async_query(
            hass, "search_help", {"query": "budget", "include_procedures": value}, assistant.context
        )
    assert error and result["error"]["code"] == "invalid_arguments"
    search.assert_not_called()
    action.assert_not_awaited()


@pytest.mark.parametrize("query", ["", "  ", "x" * 241, "line\nbreak", 123])
async def test_help_has_bounded_strict_parameters(hass, assistant, query):
    with patch("custom_components.ninebot.assistant_queries.search_help") as search:
        result, error = await async_query(hass, "search_help", {"query": query}, assistant.context)
    assert error and result["error"]["code"] == "invalid_arguments"
    search.assert_not_called()


async def test_help_corruption_does_not_break_vehicle_queries(hass, assistant):
    with patch(
        "custom_components.ninebot.assistant_queries.search_help",
        side_effect=KnowledgeUnavailable("private catalog detail must not escape"),
    ):
        result, error = await async_query(
            hass, "search_help", {"query": "statistics"}, assistant.context
        )
    assert error and result["error"]["code"] == "unsupported_capability"
    assert "private catalog" not in json.dumps(result)
    _, error = await query(hass, assistant, "get_vehicle_status")
    assert not error


@pytest.mark.parametrize("include_procedures", [False, True])
async def test_help_rechecks_consent_after_executor_read(hass, assistant, include_procedures):
    original_executor = hass.async_add_executor_job

    async def revoke_after_read(*args):
        result = await original_executor(*args)
        hass.config_entries.async_update_entry(
            assistant.entry,
            options={**assistant.entry.options, "enable_assistant": False},
        )
        return result

    with patch.object(hass, "async_add_executor_job", side_effect=revoke_after_read):
        result, error = await async_query(
            hass,
            "search_help",
            {"query": "statistics", "include_procedures": include_procedures},
            assistant.context,
        )
    assert error and result["error"]["code"] == "not_authorized"
    assert "data" not in result


async def test_stale_help_tool_is_denied_when_consent_is_disabled(hass, assistant):
    hass.config_entries.async_update_entry(
        assistant.entry, options={**assistant.entry.options, "enable_assistant": False}
    )
    with patch("custom_components.ninebot.assistant_queries.search_help") as search:
        result, error = await async_query(
            hass, "search_help", {"query": "statistics"}, assistant.context
        )
    assert error and result["error"]["code"] == "not_authorized"
    search.assert_not_called()


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


def test_unknown_error_codes_cannot_carry_exception_text_to_the_model():
    secret = "untrusted exception with /private/account and token=synthetic-secret"
    result = error_data(secret)
    assert result["code"] == "unsupported_capability"
    assert secret not in json.dumps(result)
    assert "message" in result


async def test_invalid_calls_consume_only_the_authenticated_user_read_budget(hass, assistant):
    for _ in range(20):
        result, error = await query(hass, assistant, "get_statistics", period="invalid")
        assert error and result["error"]["code"] == "invalid_arguments"
    result, error = await query(hass, assistant, "get_vehicle_status")
    assert error and result["error"]["code"] == "retry_later"
    assert result["error"]["retry_after_seconds"] > 0
    assert hass.data[LIMITS_KEY].active == 0


async def test_concurrent_calls_and_cancellation_do_not_cancel_another_reader(hass, assistant):
    ready, release = asyncio.Event(), asyncio.Event()
    entered = 0

    async def blocked(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        await release.wait()
        return {"months": [{"month": "202610", "ride_count": 2}]}

    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=blocked)):
        calls = [
            asyncio.create_task(query(hass, assistant, "get_statistics", period="this_month"))
            for _ in range(2)
        ]
        try:
            await asyncio.wait_for(ready.wait(), 5)
            result, error = await query(hass, assistant, "get_vehicle_status")
            assert error and result["error"]["code"] == "retry_later"
            calls[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await calls[0]
            assert hass.data[LIMITS_KEY].active == 1 and not calls[1].done()
            result, error = await query(hass, assistant, "get_vehicle_status")
            assert not error
            release.set()
            _, error = await calls[1]
            assert not error
        finally:
            release.set()
            await asyncio.gather(*calls, return_exceptions=True)
    assert hass.data[LIMITS_KEY].active == 0


async def test_timeout_releases_admission_and_returns_a_typed_error(hass, assistant):
    def short_timeout(seconds):
        assert seconds == 10
        # The fixture freezes monotonic time. Expire on the next loop turn
        # instead of waiting for a clock that deliberately does not advance.
        return asyncio.timeout(-1)

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    with (
        patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=blocked)),
        patch(
            "custom_components.ninebot.assistant_queries.asyncio",
            SimpleNamespace(timeout=short_timeout),
        ),
    ):
        result, error = await query(hass, assistant, "get_statistics", period="today")
    assert error and result["error"]["code"] == "timeout"
    assert hass.data[LIMITS_KEY].active == 0


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
    assert [day.isoformat() for day in period_dates("2026-10-31", now)] == ["2026-10-31"] * 2
    assert [day.isoformat() for day in period_dates("2026-11-01", now)] == ["2026-11-01"] * 2
    with pytest.raises(AssistantError, match="invalid_arguments"):
        period_dates("2026-11-02", now)


@pytest.mark.parametrize(
    "period,expected",
    [
        ("2024-02", ("2024-02-01", "2024-02-29")),
        ("2025-02", ("2025-02-01", "2025-02-28")),
        ("2025-12", ("2025-12-01", "2025-12-31")),
        ("2026-10", ("2026-10-01", "2026-10-07")),
    ],
)
def test_explicit_month_uses_calendar_bounds_and_stops_at_today(period, expected):
    assert tuple(day.isoformat() for day in period_dates(period, NOW)) == expected


async def test_verified_busy_is_retried_once_with_original_scope_context_and_deadline(
    hass, assistant
):
    original = hass.services.async_call
    attempts = []

    async def busy_once(*args, **kwargs):
        attempts.append((args, kwargs))
        assert kwargs["context"] is assistant.context
        assert hass.data[LIMITS_KEY].active == 1
        if len(attempts) == 1:
            raise HomeAssistantError(translation_domain="ninebot", translation_key="busy")
        return await original(*args, **kwargs)

    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=busy_once)):
        result, error = await query(hass, assistant, "get_statistics", period="yesterday")
    assert not error and result["data"]["ride_count"] == 2
    assert result["query"]["local_action_attempts"] == 2 and len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]
    assert len(hass.data[LIMITS_KEY].calls[assistant.context.user_id]) == 1


@pytest.mark.parametrize(
    "key,arguments,domain,error_key,attempts",
    [
        ("get_statistics", {"period": "today"}, "ninebot", "busy", 2),
        ("get_statistics", {"period": "today"}, "other", "busy", 1),
        ("get_statistics", {"period": "today"}, "ninebot", "auth", 1),
        ("get_statistics", {"period": "today"}, "ninebot", "unknown_error", 1),
        ("get_statistics", {"period": "today"}, "ninebot", "query_unavailable", 1),
        (
            "get_recorded_trips",
            {"start_date": "2026-10-06", "end_date": "2026-10-06", "cursor": "a" * 32},
            "ninebot",
            "busy",
            1,
        ),
    ],
)
async def test_retry_is_bounded_and_never_replays_cursors_or_unverified_errors(
    hass, assistant, key, arguments, domain, error_key, attempts
):
    with patch.object(
        type(hass.services),
        "async_call",
        new=AsyncMock(
            side_effect=HomeAssistantError(translation_domain=domain, translation_key=error_key)
        ),
    ) as action:
        result, error = await query(hass, assistant, key, **arguments)
    assert error and "data" not in result
    assert action.await_count == attempts
    assert hass.data[LIMITS_KEY].active == 0


async def test_revocation_before_busy_retry_prevents_second_action(hass, assistant):
    async def revoke(*args, **kwargs):
        hass.config_entries.async_update_entry(
            assistant.entry, options={**assistant.entry.options, "enable_assistant": False}
        )
        raise HomeAssistantError(translation_domain="ninebot", translation_key="busy")

    with patch.object(
        type(hass.services), "async_call", new=AsyncMock(side_effect=revoke)
    ) as action:
        result, error = await query(hass, assistant, "get_statistics", period="yesterday")
    assert error and result["error"]["code"] == "not_authorized"
    assert action.await_count == 1


@pytest.mark.parametrize(
    "period,count,status",
    [("yesterday", 2, "available"), ("today", 0, "available"), ("2026-09", None, "missing")],
)
async def test_statistics_check_uses_real_local_statistics_health_and_cited_checklist(
    hass, assistant, period, count, status
):
    original = hass.services.async_call

    async def observed(*args, **kwargs):
        assert kwargs["context"] is assistant.context
        assert hass.data[LIMITS_KEY].active == 1
        return await original(*args, **kwargs)

    with patch.object(
        type(hass.services), "async_call", new=AsyncMock(side_effect=observed)
    ) as action:
        result, error = await query(
            hass,
            assistant,
            "explain_statistics",
            period=period,
            metric="ride_count",
            language="zh-Hans",
        )
    assert not error and result["quality"] == {
        "partial": False,
        "metric": {
            "status": status,
            "reason": "available" if count is not None else "missing_month",
        },
    }
    steps = result["data"]["steps"]
    assert [step["tool"] for step in steps] == [
        "ninebot__get_statistics",
        "ninebot__get_sync_health",
        "ninebot__search_help",
    ]
    assert all(not step["error"] for step in steps)
    assert steps[0]["result"]["data"]["ride_count"] == count
    assert steps[1]["result"]["data"]["background_enabled"] is False
    help_data = steps[2]["result"]["data"]
    assert help_data["matches"][0]["source_id"] == "statistics_missing"
    assert help_data["matches"][0]["citation"]
    assert help_data["procedures"][0]["id"] == "SK05"
    assert action.await_count == 2
    assert len(hass.data[LIMITS_KEY].calls[assistant.context.user_id]) == 4
    assert hass.data[LIMITS_KEY].active == 0


async def test_statistics_check_fixes_business_period_before_month_rollover(hass, assistant):
    before = datetime(2026, 10, 31, 15, 59, 59, tzinfo=UTC)
    after = before + timedelta(seconds=2)
    clock = Mock(side_effect=lambda: before if clock.call_count == 1 else after)
    with patch("custom_components.ninebot.assistant_queries.dt_util.utcnow", clock):
        result, error = await query(
            hass, assistant, "explain_statistics", period="this_month", metric="ride_count"
        )
    assert not error
    assert result["query"]["end_date"] == "2026-10-31"
    child = result["data"]["steps"][0]["result"]
    assert child["query"]["as_of"] == after.isoformat()
    assert child["query"]["period"] == "2026-10"
    assert child["query"]["end_date"] == "2026-10-31"


async def test_statistics_check_does_not_relabel_a_new_day_month_summary(hass, assistant):
    before = datetime(2026, 10, 6, 15, 59, 59, tzinfo=UTC)
    after = before + timedelta(seconds=2)
    clock = Mock(side_effect=lambda: before if clock.call_count == 1 else after)
    with patch("custom_components.ninebot.assistant_queries.dt_util.utcnow", clock):
        result, error = await query(
            hass, assistant, "explain_statistics", period="this_month", metric="ride_count"
        )
    assert not error and result["quality"]["partial"]
    assert result["query"]["end_date"] == "2026-10-06"
    assert result["data"]["steps"][0]["result"]["error"]["code"] == "retry_later"


async def test_statistics_check_keeps_partial_errors_without_inventing_zero(hass, assistant):
    original = hass.services.async_call

    async def missing(*args, **kwargs):
        if args[1] == "get_statistics":
            raise HomeAssistantError(
                translation_domain="ninebot", translation_key="query_unavailable"
            )
        return await original(*args, **kwargs)

    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=missing)):
        result, error = await query(
            hass, assistant, "explain_statistics", period="yesterday", metric="ride_count"
        )
    assert not error and result["quality"]["partial"]
    assert result["quality"]["metric"] == {"status": "unknown", "reason": "statistics_query_failed"}
    assert result["data"]["steps"][0]["error"] is True
    assert result["data"]["steps"][1]["error"] is False


async def test_statistics_check_discards_all_evidence_when_consent_is_revoked(hass, assistant):
    original = hass.services.async_call

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[1] == "sync_history":
            hass.config_entries.async_update_entry(
                assistant.entry, options={**assistant.entry.options, "enable_assistant": False}
            )
        return result

    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=revoke)):
        result, error = await query(
            hass, assistant, "explain_statistics", period="yesterday", metric="ride_count"
        )
    assert error and result["error"]["code"] == "not_authorized" and "data" not in result


async def test_statistics_check_stops_when_child_admission_is_limited(hass, assistant):
    for _ in range(18):
        assert not (await query(hass, assistant, "get_vehicle_status"))[1]
    original = hass.services.async_call
    with patch.object(type(hass.services), "async_call", new=AsyncMock(wraps=original)) as action:
        result, error = await query(
            hass, assistant, "explain_statistics", period="yesterday", metric="ride_count"
        )
    assert not error and result["quality"]["partial"]
    assert len(result["data"]["steps"]) == 2
    assert result["data"]["steps"][1]["result"]["error"]["retry_after_seconds"] > 0
    assert action.await_count == 1 and hass.data[LIMITS_KEY].active == 0


async def test_statistics_check_does_not_claim_evidence_when_all_steps_are_limited(hass, assistant):
    for _ in range(19):
        assert not (await query(hass, assistant, "get_vehicle_status"))[1]
    result, error = await query(
        hass, assistant, "explain_statistics", period="yesterday", metric="ride_count"
    )
    assert error and result["error"]["code"] == "retry_later"
    assert result["error"]["retry_after_seconds"] > 0 and "data" not in result


async def test_statistics_check_cancellation_releases_the_child_slot(hass, assistant):
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        assert hass.data[LIMITS_KEY].active == 1
        entered.set()
        await asyncio.Event().wait()

    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=blocked)):
        task = asyncio.create_task(
            query(hass, assistant, "explain_statistics", period="yesterday", metric="ride_count")
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert hass.data[LIMITS_KEY].active == 0
