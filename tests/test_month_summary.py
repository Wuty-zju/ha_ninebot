"""Offline monthly chart and coverage contracts; no cloud requests."""

import json
from pathlib import Path

import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.month_summary import summarize_month
from custom_components.ninebot.travel import parse_rides

FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"


def test_recorded_partial_list_is_not_a_month_aggregate():
    raw = json.loads((FIXTURES / "travel-nonempty.json").read_text())
    summary = adapters.travel(raw, "202609").summary
    assert summary is not None
    assert summary.ride_count == 128 and summary.returned_count == 20
    assert summary.list_complete is False and summary.coverage == 20 / 128
    assert summary.mileage_km == 303.9 and summary.energy_wh == 7210
    assert summary.returned_distance_m == pytest.approx(28800)
    assert summary.returned_duration_s == 5136 and summary.returned_energy_wh == 670
    assert summary.chart_status == "invalid" and summary.daily_mileage == ()
    assert "month_list_partial" in summary.warnings


@pytest.mark.parametrize(
    "month,days", [("202602", 28), ("202402", 29), ("202609", 30), ("202610", 31)]
)
def test_synthetic_calendar_chart_dates_and_decimal_sum(month, days):
    raw = {"detail": ["0.10"] * days, "total_mileages": days / 10, "times": 0, "list": None}
    summary = adapters.travel(raw, month).summary
    assert summary is not None
    assert summary.chart_status == "valid"
    assert len(summary.daily_mileage) == days
    assert summary.daily_mileage[0].day.isoformat() == f"{month[:4]}-{month[4:]}-01"
    assert summary.daily_mileage[-1].day.day == days
    assert summary.list_complete is True and summary.coverage == 1
    assert summary.returned_energy_wh == 0


@pytest.mark.parametrize(
    "chart,status",
    [
        (None, "not_reported"),
        ("0", "invalid"),
        (["0"] * 29, "invalid"),
        (["NaN"] * 30, "invalid"),
        ([True] * 30, "invalid"),
        ([-1] * 30, "invalid"),
        ([{}] * 30, "invalid"),
        (["1"] * 30, "inconsistent"),
    ],
)
def test_invalid_chart_never_becomes_zero_or_fabricated_points(chart, status):
    summary = summarize_month({"detail": chart, "total_mileages": 0}, "202609", ())
    assert summary.chart_status == status
    assert len(summary.daily_mileage) == (30 if status == "inconsistent" else 0)
    assert summary.list_complete is None and summary.coverage is None


def test_valid_chart_with_unavailable_total_keeps_provenance():
    summary = summarize_month({"detail": ["0"] * 30}, "202609", ())
    assert summary.chart_status == "total_unavailable"
    assert len(summary.daily_mileage) == 30


@pytest.mark.parametrize(
    "count,rows,complete,coverage,warning",
    [
        (2, [{"travel_id": "a"}, {"travel_id": "b"}], True, 1, None),
        (2, [{"travel_id": "a"}, {"travel_id": "a"}], None, None, "ride_identity_incomplete"),
        (1, [{}], None, None, "ride_identity_incomplete"),
        (0, [{"travel_id": "a"}], None, None, "reported_count_inconsistent"),
        (True, [], None, None, "reported_count_unavailable"),
        (1.5, [], None, None, "reported_count_unavailable"),
        (2, [{"travel_id": "a"}], False, 0.5, "month_list_partial"),
    ],
)
def test_coverage_does_not_invent_missing_or_duplicate_ids(
    count, rows, complete, coverage, warning
):
    raw = {"times": count, "list": rows}
    summary = summarize_month(raw, "202609", parse_rides(raw, "202609"))
    assert summary.list_complete is complete and summary.coverage == coverage
    if warning:
        assert warning in summary.warnings
    if rows:
        assert summary.returned_distance_m is None
        assert summary.returned_duration_s is None
        assert summary.returned_energy_wh is None


async def test_action_returns_chart_from_same_cached_month_without_new_queries(
    hass, entry, app_client, freezer, enable_custom_integrations
):
    from datetime import UTC, datetime

    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    freezer.move_to(datetime(2026, 9, 26, tzinfo=UTC))
    # Synthetic chart replaces redacted daily schedule, keeping recorded shape.
    raw = json.loads((FIXTURES / "travel-nonempty.json").read_text())
    raw["detail"] = ["10.13"] * 30
    app_client.async_get_travel.return_value = raw
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in d.identifiers
    )
    registry = er.async_get(hass)
    for key, value in [("month_list_coverage", 15.625)]:
        row_id = registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}")
        assert registry.async_get(row_id).disabled_by is None
        assert float(hass.states.get(row_id).state) == value
        assert hass.states.get(row_id).attributes["returned_rides"] == 20
    app_client.async_get_travel.reset_mock()
    response = await hass.services.async_call(
        "ninebot",
        "get_trips",
        {"device_id": device.id, "month": "202609"},
        blocking=True,
        return_response=True,
    )
    assert response["schema_version"] == 2
    assert response["daily_mileage_status"] == "valid"
    assert len(response["daily_mileage"]) == 30
    assert response["daily_mileage"][0] == {"date": "2026-09-01", "distance_km": 10.13}
    assert response["month_energy_raw"] == response["month_energy_wh"] == 7210
    assert response["rides"][0]["energy_raw"] == response["rides"][0]["energy_wh"] == 5
    assert response["coverage"]["list_complete"] is False
    json.dumps(response, allow_nan=False)
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()
