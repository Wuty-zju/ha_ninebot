"""Offline feature configuration, privacy, budgets and model identity."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import adapters, compat
from custom_components.ninebot.battery_parameters import BatteryParameters
from custom_components.ninebot.config_flow import parameter_validator
from custom_components.ninebot.debug_view import DEBUG_ATTRIBUTES, debug_view, numeric_observation
from custom_components.ninebot.diagnostics import (
    MAX_DIAGNOSTIC_VEHICLES,
    _history_evidence,
    _month_evidence,
    async_get_config_entry_diagnostics,
)
from custom_components.ninebot.exceptions import ErrorKind
from custom_components.ninebot.history_plan import HistoryPlan
from custom_components.ninebot.history_progress import HistoryProgress
from custom_components.ninebot.models import Battery, BatteryInfo, Freshness, VehicleProfile
from custom_components.ninebot.raw import Endpoint, RawRecord, build_record
from custom_components.ninebot.statistics_store import StoredMonth
from custom_components.ninebot.storage import ModelStorage

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_debug_opt_in_reuses_identity_and_never_adds_query_demand(hass, entry, app_client):
    hass.config_entries.async_update_entry(entry, options={"debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    row_id = er.async_get(hass).async_get_entity_id(
        "sensor", "ninebot", "SyntheticSN_raw_data_summary"
    )
    before = co.demand("SyntheticSN")
    methods = [
        app_client.async_get_status,
        app_client.async_get_battery,
        app_client.async_get_travel,
    ]
    counts = [method.await_count for method in methods]
    snapshot = co.data["SyntheticSN"]
    # Deliberately hostile text in approved paths must not escape into this view.
    co.data["SyntheticSN"] = replace(
        snapshot,
        profile=VehicleProfile(
            "SyntheticSN",
            "Scooter",
            "MODEL",
            "https://example.invalid?token=PRIVATE",
            {"businessType": "PRIVATE"},
        ),
        status=replace(
            snapshot.status,
            latitude=31.12345,
            longitude=120.98765,
            charge_remaining="PRIVATE PASSWORD",
            observations={"permissions": "PRIVATE"},
        ),
        battery=BatteryInfo(
            tuple(
                Battery(
                    f"PRIVATE-{i}", True, 79.6, 24, None, False, "PRIVATE", "PRIVATE", "PRIVATE"
                )
                for i in range(5)
            ),
            310,
        ),
    )
    co.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(row_id)
    assert state.state == "ok"
    assert state.attributes["parsed"]["battery"]["charging_power_w"] == 310
    assert len(state.attributes["parsed"]["battery"]["packs"]) == 4
    encoded = json.dumps(dict(state.attributes), ensure_ascii=False, allow_nan=False)
    assert len(encoded.encode()) < 16384
    assert not any(
        value in encoded for value in ["PRIVATE", "31.12345", "120.98765", "SyntheticSN"]
    )
    assert counts == [method.await_count for method in methods]
    assert co.demand("SyntheticSN") == before
    assert DEBUG_ATTRIBUTES <= hass.data["sensor"].get_entity(row_id)._unrecorded_attributes
    hass.config_entries.async_update_entry(entry, options={"debug_mode": False})
    await hass.async_block_till_done()
    state = hass.states.get(row_id)
    assert state is None
    row = er.async_get(hass).async_get(row_id)
    assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert row.hidden_by is er.RegistryEntryHider.INTEGRATION
    assert er.async_get(hass).async_get(row_id).unique_id == "SyntheticSN_raw_data_summary"


async def test_options_and_number_share_rated_parameters(hass, entry, app_client):
    hass.config_entries.async_update_entry(entry, options={"enable_estimation": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    model = entry.runtime_data.models.model("SyntheticSN")
    assert model.nominal is None
    form = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "polling_profile": "slower",
            "debug_mode": True,
            "configure_model": True,
        },
    )
    assert form["step_id"] == "model_vehicle"
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"model_vehicle": "SyntheticSN"}
    )
    assert form["step_id"] == "model_parameters"
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"voltage": 72, "capacity": 20}
    )
    assert result["type"].value == "create_entry"
    await hass.async_block_till_done()
    store = entry.runtime_data.models
    assert store.model("SyntheticSN").nominal == 1.44
    assert "voltage" not in entry.options and "capacity" not in entry.options
    assert "configure_model" not in entry.options
    registry = er.async_get(hass)
    number_id = registry.async_get_entity_id("number", "ninebot", "SyntheticSN_battery_capacity")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 30}, blocking=True
    )
    assert store.model("SyntheticSN").capacity == 30
    assert (
        registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_estimation_quality") is None
    )


async def test_model_transaction_rejects_partial_configuration(hass):
    store = ModelStorage(hass, "synthetic")
    store.schedule_save = MagicMock()
    old = store.model("car")
    await store.async_load()
    store.configure("car", {"voltage": 72})
    store.schedule_save.reset_mock()
    old = store.model("car")
    with pytest.raises(ValueError):
        store.configure("car", {"voltage": 60, "capacity": -1})
    assert store.model("car") is old and old.voltage == 72
    store.schedule_save.assert_not_called()
    store.configure("car", {"voltage": 72})
    assert store.model("car") is old


@pytest.mark.parametrize("value", [True, "NaN", {}, -1, 301])
def test_model_flow_parameter_bounds(value):
    with pytest.raises(vol.Invalid):
        parameter_validator(300)(value)


def test_recorder_capability_fallback_and_raw_value_safety():
    with patch.object(compat, "Entity", SimpleNamespace()):
        assert compat.unrecorded_attributes(DEBUG_ATTRIBUTES) == frozenset()
    with patch.object(compat, "Entity", SimpleNamespace(_unrecorded_attributes=frozenset())):
        assert compat.unrecorded_attributes(DEBUG_ATTRIBUTES) == DEBUG_ATTRIBUTES
    for value in ["private-token", "13400000000", "9" * 256, {}, 10**12]:
        assert numeric_observation(value) is None
    assert numeric_observation("100") == "100"
    assert numeric_observation(False) is False


async def test_debug_health_states_follow_group_freshness(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    snapshot = co.data["SyntheticSN"]
    with patch.object(co, "fresh", side_effect=lambda sn, group: group == "status"):
        assert debug_view(snapshot, co, BatteryParameters())[0] == "partial"
    with patch.object(co, "fresh", return_value=False):
        assert debug_view(snapshot, co, BatteryParameters())[0] == "stale"
        broken = replace(snapshot, status_freshness=Freshness(error=ErrorKind.CONNECTION))
        assert debug_view(broken, co, BatteryParameters())[0] == "error"


async def test_debug_gate_reloads_without_losing_identity_or_user_disable(hass, entry, app_client):
    hass.config_entries.async_update_entry(entry, options={"debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    keys = (
        "raw_data_summary",
        "history_sync_state",
        "history_sync_last_success",
        "month_list_coverage",
    )
    ids = {
        key: registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}") for key in keys
    }
    registry.async_update_entity(
        ids["month_list_coverage"], disabled_by=er.RegistryEntryDisabler.USER
    )
    hass.config_entries.async_update_entry(entry, options={"debug_mode": False})
    await hass.async_block_till_done()
    for key in keys:
        row = registry.async_get(ids[key])
        assert row.disabled_by is (
            er.RegistryEntryDisabler.USER
            if key == "month_list_coverage"
            else er.RegistryEntryDisabler.INTEGRATION
        )
        assert hass.states.get(ids[key]) is None
    hass.config_entries.async_update_entry(entry, options={"debug_mode": True})
    await hass.async_block_till_done()
    for key in keys:
        assert registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}") == ids[key]
        if key == "month_list_coverage":
            assert registry.async_get(ids[key]).disabled_by is er.RegistryEntryDisabler.USER
        else:
            assert registry.async_get(ids[key]).disabled_by is None
            assert hass.states.get(ids[key]) is not None


async def test_rated_parameters_allow_unfilled_partial_and_explicit_clear(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for values, expected in (
        ({}, (None, None)),
        ({"voltage": 72}, (72, None)),
        ({"capacity": 38}, (None, 38)),
        ({}, (None, None)),
    ):
        form = await hass.config_entries.options.async_init(entry.entry_id)
        form = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {"configure_model": True},
        )
        form = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {"model_vehicle": "SyntheticSN"},
        )
        assert all(not isinstance(key, vol.Required) for key in form["data_schema"].schema)
        result = await hass.config_entries.options.async_configure(form["flow_id"], values)
        assert result["type"].value == "create_entry"
        await hass.async_block_till_done()
        model = entry.runtime_data.models.model("SyntheticSN")
        assert (model.voltage, model.capacity) == expected
        assert model.nominal is None


@pytest.mark.parametrize(
    "info,expected",
    [
        (
            SimpleNamespace(identifiers={("ninebot", "vehicle")}),
            frozenset({("ninebot", "vehicle")}),
        ),
        ({"identifiers": {("ninebot", "vehicle")}}, frozenset({("ninebot", "vehicle")})),
        (
            SimpleNamespace(identifiers={("ninebot", 123), ("ninebot", "vehicle")}),
            frozenset({("ninebot", "vehicle")}),
        ),
        ({"identifiers": None}, frozenset()),
    ],
)
def test_device_info_identifiers_support_native_and_legacy_shapes(info, expected):
    assert compat.device_identifiers(info) == expected


@pytest.mark.parametrize("required", [False, True])
def test_conditional_field_keeps_plain_schema_on_older_core(required):
    from homeassistant import data_entry_flow

    with patch.object(data_entry_flow, "Required" if required else "Optional", None, create=True):
        field = compat.conditional_field("budget", 60, when="enabled", required=required)
    assert not hasattr(field, "visible")
    assert compat.validation.Schema({field: int})({}) == {"budget": 60}


@pytest.mark.parametrize(
    "raw,state,count,returned,unique,coverage,warnings",
    [
        ({"times": 0}, "missing", 0, 0, 0, 1.0, []),
        ({"times": 0, "list": None}, "null", 0, 0, 0, 1.0, []),
        ({"times": 0, "list": []}, "empty", 0, 0, 0, 1.0, []),
        ({"times": 2, "list": []}, "empty", 2, 0, 0, 0.0, ["month_list_partial"]),
        (
            {"times": "PRIVATE", "list": None},
            "null",
            None,
            0,
            0,
            None,
            ["reported_count_unavailable"],
        ),
        (
            {"times": 2, "list": [{"travel_id": "PRIVATE_RIDE"}] * 2},
            "valid",
            2,
            2,
            1,
            None,
            ["ride_identity_incomplete"],
        ),
        ({"times": 2, "list": "PRIVATE"}, "invalid", 2, None, None, None, None),
        ({"times": 2, "list": ["PRIVATE"]}, "invalid", 2, None, None, None, None),
    ],
)
def test_diagnostics_month_presence_and_honest_counts(
    raw, state, count, returned, unique, coverage, warnings
):
    stamp = datetime(2026, 11, 17, tzinfo=UTC)
    record = build_record(Endpoint.TRAVEL, raw, stamp, "202611")
    evidence = _month_evidence("202611", record, None, None, None)
    assert evidence["raw_list"]["state"] == state
    assert evidence["raw_list"]["server_count"] == count
    assert evidence["raw_list"]["received_at"] == stamp.isoformat()
    assert evidence["raw_list"]["parse_status"] == ("invalid" if state == "invalid" else "accepted")
    summary = evidence["summary"]
    assert summary["server_count"] == (None if state == "invalid" else count)
    assert summary["returned_count"] == returned
    assert summary["unique_count"] == unique
    assert summary["coverage"] == coverage
    assert summary["warnings"] == warnings
    assert "PRIVATE" not in json.dumps(evidence, allow_nan=False)


def test_diagnostics_unobserved_and_retained_summary_have_separate_provenance():
    stamp = datetime(2026, 11, 17, tzinfo=UTC)
    missing = _month_evidence("202611", None, None, None, None)
    assert missing["raw_list"]["state"] == "unobserved"
    assert missing["summary"]["source"] == "unobserved"
    assert missing["summary"]["server_count"] is None
    assert missing["summary"]["returned_count"] is None
    travel = adapters.travel({"times": 0, "list": None}, "202611")
    cached = _month_evidence("202611", None, travel, stamp, None)
    assert cached["raw_list"]["state"] == "unobserved"
    assert cached["summary"]["source"] == "snapshot"
    assert cached["summary"]["coverage"] == 1
    newer = build_record(
        Endpoint.TRAVEL, {"times": 2, "list": "PRIVATE"}, stamp + timedelta(seconds=1), "202611"
    )
    refused = _month_evidence("202611", newer, travel, stamp, None)
    assert refused["raw_list"]["parse_status"] == "invalid"
    assert refused["summary"] == cached["summary"]
    stored = StoredMonth("202611", stamp.isoformat(), 1, 0, 0, 0, 0, (), "not_reported", True, ())
    restored = _month_evidence("202611", None, None, None, stored)
    assert restored["summary"]["source"] == "statistics_projection"
    assert restored["summary"]["server_count"] == 0
    assert restored["summary"]["returned_count"] is None
    assert restored["summary"]["unique_count"] is None
    assert restored["summary"]["coverage"] is None
    assert restored["raw_list"]["state"] == "unobserved"


def test_diagnostics_reuses_accepted_summary_and_bounds_warning_whitelist():
    stamp = datetime(2026, 11, 17, tzinfo=UTC)
    raw = {"times": 0, "list": None}
    record = build_record(Endpoint.TRAVEL, raw, stamp, "202611")
    travel = adapters.travel(raw, "202611")
    travel = replace(
        travel,
        summary=replace(travel.summary, warnings=("PRIVATE", "month_list_partial") * 100),
    )
    with patch("custom_components.ninebot.diagnostics.adapters.travel") as parse:
        evidence = _month_evidence("202611", record, travel, stamp, None)
    parse.assert_not_called()
    assert evidence["summary"]["warnings"] == ["month_list_partial"]
    assert "PRIVATE" not in json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize("complete,detail_percent", [(False, None), (True, 100)])
def test_diagnostics_history_zero_denominator_and_cache_only_scope(complete, detail_percent):
    stamp = datetime(2026, 11, 17, tzinfo=UTC)
    progress = HistoryProgress(
        state="synced",
        scanned_months=0,
        verified_months=0,
        total_months=0,
        gap_months=0,
        discovered=0,
        saved=0,
        missing=0,
        unmapped=0,
        quality_pending=0,
        simplified=0,
        discovery_complete=complete,
        active_months=0,
        last_success=stamp,
    )
    plan = HistoryPlan.create("a" * 32, "b" * 64, "202610", "202611", stamp.isoformat())
    preservation = SimpleNamespace(
        progress={"PRIVATE_SN": progress},
        bootstrap=SimpleNamespace(snapshots={"PRIVATE_SN": (plan, None)}, incremental_snapshots={}),
    )
    evidence = _history_evidence(preservation, "PRIVATE_SN")
    assert evidence["scan_percent"] is None
    assert evidence["detail_percent"] == detail_percent
    assert evidence["discovered"] == evidence["saved"] == 0
    assert evidence["discovery_complete"] is complete
    assert evidence["baseline_scope"] == {
        "source": "bootstrap_cache",
        "start_month": "202610",
        "end_month": "202611",
        "next_month": "202611",
    }
    assert evidence["incremental_scope"]["source"] == "unobserved"
    unknown = _history_evidence(preservation, "unobserved")
    assert unknown["state"] == "unknown"
    assert unknown["discovered"] is unknown["discovery_complete"] is None
    preservation.progress["PRIVATE_SN"] = replace(progress, state="PRIVATE", phase="PRIVATE")
    guarded = _history_evidence(preservation, "PRIVATE_SN")
    assert guarded["state"] == "unknown" and guarded["phase"] is None
    assert not any(
        value in json.dumps([evidence, guarded], allow_nan=False)
        for value in ("PRIVATE", plan.plan_id, plan.vehicle)
    )


async def test_diagnostics_isolate_vehicle_months_without_queries(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    stamp = datetime(2026, 11, 17, tzinfo=UTC)
    original = co.data["SyntheticSN"]
    co.data["PRIVATE_SECOND_SN"] = replace(
        original, profile=VehicleProfile("PRIVATE_SECOND_SN", "PRIVATE_NAME", "model"), travel=None
    )
    for sn, month, raw in (
        ("SyntheticSN", "202611", {"times": 0, "list": None}),
        ("SyntheticSN", "202610", {"times": 2, "list": []}),
        (
            "PRIVATE_SECOND_SN",
            "202611",
            {
                "times": 1,
                "list": [{"travel_id": "PRIVATE_RIDE", "loc": {"lat": 31.12345, "lon": 120.98765}}],
            },
        ),
        # Retained older months must not enlarge the exported scope or be decoded.
        ("PRIVATE_SECOND_SN", "202609", {"times": 999, "list": []}),
    ):
        co.raw.put(build_record(Endpoint.TRAVEL, raw, stamp, month), sn, month)
    counts = list(app_client.mock_calls)
    demand = {sn: co.demand(sn) for sn in co.data}
    main_thread = get_ident()
    payload = RawRecord.payload
    decoded = []

    def off_loop(record):
        assert get_ident() != main_thread
        decoded.append(record.query_month)
        return payload(record)

    # The optional failure accessor is absent on older coordinators. On the
    # upgraded coordinator it remains a local, controlled projection.
    failure = {
        "stage": "normalize",
        "endpoint": "travel",
        "error_kind": "protocol",
        "retryable": False,
    }
    with (
        patch("custom_components.ninebot.diagnostics.dt_util.utcnow", return_value=stamp),
        patch.object(RawRecord, "payload", off_loop),
        patch.object(
            co,
            "query_failure",
            lambda sn, group: failure if sn == "SyntheticSN" and group == "travel" else None,
            create=True,
        ),
        patch.object(
            co.statistics.archive,
            "async_history_progress",
            side_effect=AssertionError("no refresh"),
        ),
    ):
        diag = await async_get_config_entry_diagnostics(hass, entry)
    one, two = diag["vehicles"]
    assert [one["vehicle_index"], two["vehicle_index"]] == [1, 2]
    first = one["travel_statistics"]["months"]
    second = two["travel_statistics"]["months"]
    assert [row["month"] for row in first] == ["202611", "202610"]
    assert first[0]["raw_list"]["state"] == "null"
    assert first[1]["summary"]["warnings"] == ["month_list_partial"]
    assert second[0]["summary"]["unique_count"] == 1
    assert second[1]["raw_list"]["state"] == "unobserved"
    assert one["groups"]["travel"]["failure_context"] == failure
    assert two["groups"]["travel"]["failure_context"] is None
    assert sorted(decoded) == ["202610", "202611", "202611"]
    assert app_client.mock_calls == counts
    assert {sn: co.demand(sn) for sn in co.data} == demand
    encoded = json.dumps(diag, allow_nan=False)
    assert len(encoded.encode()) < 131072
    assert not any(
        value in encoded for value in ("PRIVATE", "SyntheticSN", "31.12345", "120.98765")
    )
    assert "month_count" in diag["travel_statistics"]  # Existing account totals remain.


async def test_diagnostics_vehicle_cap_and_optional_failure_accessor(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    snapshot = co.data["SyntheticSN"]
    co.data = {
        f"PRIVATE_{i}": replace(
            snapshot, profile=VehicleProfile(f"PRIVATE_{i}", "PRIVATE", "model"), travel=None
        )
        for i in range(MAX_DIAGNOSTIC_VEHICLES + 2)
    }
    with patch.object(co, "query_failure", None, create=True):
        diag = await async_get_config_entry_diagnostics(hass, entry)
    assert len(diag["vehicles"]) == MAX_DIAGNOSTIC_VEHICLES
    assert diag["diagnostic_limits"]["vehicles_truncated"] is True
    assert diag["diagnostic_limits"]["vehicle_count"] == MAX_DIAGNOSTIC_VEHICLES + 2
    assert diag["diagnostic_limits"]["max_raw_payloads_per_vehicle"] == 2
    assert all(row["groups"]["travel"]["failure_context"] is None for row in diag["vehicles"])
    assert len(json.dumps(diag, allow_nan=False).encode()) < 262144
