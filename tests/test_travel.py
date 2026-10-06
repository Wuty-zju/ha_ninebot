import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.exceptions import NinebotError
from custom_components.ninebot.raw import Endpoint
from custom_components.ninebot.ride_models import FieldState, Ride, SpeedSample
from custom_components.ninebot.travel import (
    latest_ride,
    merge_detail,
    opaque_id,
    parse_ride,
    parse_rides,
    parse_track,
    timestamp,
)

FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"


def test_recorded_shapes_units_timestamps_id_mapping_and_correct_average():
    month = json.loads((FIXTURES / "travel-nonempty.json").read_text())
    detail = json.loads((FIXTURES / "trip-detail.json").read_text())
    summary = parse_rides(month, "202609")[0]
    assert summary.ride_id == summary.detail_id == "fixture-ride-01"
    assert summary.duration_s == 83
    assert summary.distance_m == 300
    assert summary.server_max_speed_m_s == pytest.approx(23 / 3.6)
    assert summary.average_speed_m_s == pytest.approx(300 / 83)
    assert summary.ended_at - summary.started_at == __import__("datetime").timedelta(seconds=83)
    assert summary.track_points == ()
    parsed = parse_ride(detail, "202609", source=Endpoint.TRIP_DETAIL)
    merged = merge_detail(summary, parsed)
    assert merged.ride_id == summary.ride_id
    assert merged.detail_id == summary.detail_id
    assert merged.server_average_speed_raw == 0
    assert merged.average_speed_m_s == summary.average_speed_m_s
    assert merged.server_max_speed_m_s == summary.server_max_speed_m_s
    assert len(merged.track_points) == len(merged.speed_samples) == 5
    assert merged.track_points[0].latitude == 30
    assert merged.track_points[0].longitude == 120
    assert merged.start_location == merged.track_points[0]
    assert merged.end_location == merged.track_points[-1]
    assert merged.total_track_points == 5
    assert merged.sample_mean_speed_m_s is merged.sample_max_speed_m_s is None
    assert all(
        point.timestamp is None
        and point.speed_m_s is None
        and point.distance_delta_m is None
        and point.coordinate_system == "unknown"
        for point in merged.track_points
    )
    assert not merged.issues
    month["list"].reverse()
    normalized = adapters.travel(month, "202609")
    assert normalized.last_ride.ride_id == "fixture-ride-01"
    assert normalized.mileage == float(month["total_mileages"])


def test_overall_speed_sample_mean_and_server_max_are_different_quantities():
    ride = Ride(
        "202609",
        Endpoint.TRIP_DETAIL,
        distance_m=10000,
        duration_s=3600,
        server_max_speed_m_s=25,
        speed_samples=(SpeedSample(0, 10, 10), SpeedSample(1, 20, 20)),
    )
    assert ride.average_speed_m_s == pytest.approx(10000 / 3600)
    assert ride.sample_mean_speed_m_s == 15
    assert ride.sample_max_speed_m_s == 20
    assert ride.server_max_speed_m_s == 25
    assert replace(ride, distance_m=0).average_speed_m_s == 0
    assert replace(ride, duration_s=0).average_speed_m_s is None
    assert replace(ride, distance_m=None).average_speed_m_s is None
    assert replace(ride, speed_samples=()).sample_mean_speed_m_s is None
    assert replace(ride, speed_samples=()).sample_max_speed_m_s is None
    assert replace(ride, track_points=()).start_location is None
    assert replace(ride, track_points=()).end_location is None


@pytest.mark.parametrize("value", [True, False, 1.2, -1, {}, "", "x" * 257, "unsafe\nvalue"])
def test_invalid_ids_are_not_coerced(value):
    assert opaque_id(value) is None


def test_id_aliases_no_guessed_equivalence_conflicts_and_legacy_order():
    assert opaque_id(123) == "123"
    legacy = parse_ride({"id": "old", "mileages": 0}, "202609")
    assert legacy.ride_id == "old" and legacy.detail_id is None
    assert latest_ride((legacy,)) is None
    assert parse_ride({"detail_id": "d"}, "202609").ride_id == "d"
    conflict = parse_ride({"travel_id": "a", "id": "b", "detail_id": "c"}, "202609")
    assert conflict.detail_id is None
    assert "conflicting_ride_ids" in conflict.issues
    assert "conflicting_detail_ids" in conflict.issues
    with pytest.raises(NinebotError):
        merge_detail(legacy, replace(legacy, ride_id="different"))
    changed = merge_detail(legacy, replace(legacy, distance_m=500, source=Endpoint.TRIP_DETAIL))
    assert changed.distance_m == 500
    assert "detail_corrected_distance_m" in changed.issues


@pytest.mark.parametrize("value", [True, 123.0, 0, 1700000000000, "1700000000", "garbage"])
def test_timestamp_contract_does_not_guess_units(value):
    assert timestamp(value) is None


def test_formatted_timestamps_conflicts_order_and_invalid_quantities():
    assert timestamp("2026-09-03 08:00:00", formatted=True) == datetime(2026, 9, 3, tzinfo=UTC)
    assert timestamp("2026-99-03 08:00:00", formatted=True) is None
    assert timestamp("1999-09-03 08:00:00", formatted=True) is None
    assert timestamp("x", formatted=True) is None
    invalid = parse_ride(
        {
            "start_time": 1790000000,
            "end_time": 1780000000,
            "mileages": -1,
            "duration": True,
            "speed": [1, 2],
        },
        "202609",
    )
    assert invalid.started_at is invalid.ended_at is None
    assert invalid.distance_m is invalid.duration_s is invalid.server_max_speed_m_s is None
    assert "reversed_timestamps" in invalid.issues
    formatted = parse_ride(
        {
            "start_time_format": "2026-09-03 08:00:00",
            "end_time_format": "2026-09-03 08:00:02",
            "duration": 10,
        },
        "202609",
    )
    assert "duration_time_difference" in formatted.issues
    conflicting = parse_ride(
        {"end_time": 1790000000, "end_time_format": "2026-09-03 08:00:00", "start_time": "invalid"},
        "202609",
    )
    assert "conflicting_time_representations" in conflicting.issues
    assert "invalid_timestamp" in conflicting.issues


def test_track_explicit_syntax_valid_zero_invalid_rows_and_truncation():
    points, total, issues = parse_track("0,0,0,0;120,30,10,80;", 2)
    assert len(points) == total == 2 and not issues
    assert points[0].latitude == points[0].longitude == 0
    for value in (None, ""):
        assert parse_track(value, 1) == ((), 0, ())
    assert parse_track([[120, 30]], 1)[2] == ("unverified_track_format",)
    points, total, issues = parse_track("180,91,1,1;nan,30,1,1;0,0,inf,1;0,0,1,1;;wrong", 10)
    assert len(points) == 1 and points[0].sequence == 3
    assert total == 6 and issues == ("invalid_track_points",)
    ride = parse_ride(
        {"trail": "120,30,1,5;121,31,2,6"}, "202609", source=Endpoint.TRIP_DETAIL, max_points=1
    )
    assert len(ride.track_points) == 1 and ride.track_truncated and ride.total_track_points == 2
    with pytest.raises(ValueError):
        parse_track("", 0)
    with pytest.raises(NinebotError):
        parse_track("a" * 1048577, 1)


@pytest.mark.parametrize(
    "payload", [{"list": {}}, {"list": [None]}, {"list": [{}] * 1001}, {"month": "202608"}]
)
def test_invalid_month_response_is_not_an_empty_success(payload):
    with pytest.raises(NinebotError):
        parse_rides(payload, "202609")


def test_empty_and_start_time_only_ordering():
    assert parse_rides({}, "202609") == ()
    one = parse_ride({"start_time": 1790000000, "travel_id": "one"}, "202609")
    two = parse_ride({"start_time": 1790000001, "travel_id": "two"}, "202609")
    assert latest_ride((two, one)).ride_id == "two"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, FieldState.MISSING),
        ({"ec": None}, FieldState.NULL),
        ({"ec": False}, FieldState.INVALID),
        ({"ec": -1}, FieldState.INVALID),
        ({"ec": 0}, FieldState.VALID),
    ],
)
def test_explicit_field_states_and_partial_metric_provenance(raw, expected):
    summary = parse_ride({"travel_id": "a", "ec": 10, "speed": 20, "avg_speed": 3}, "202609")
    detail = parse_ride(raw, "202609", source=Endpoint.TRIP_DETAIL)
    assert dict(detail.field_states)["energy_raw"] is expected
    merged = merge_detail(summary, detail)
    assert dict(merged.detail_field_states)["energy_raw"] is expected
    assert merged.energy_raw == (0 if expected is FieldState.VALID else 10)
    assert merged.server_max_speed_m_s == pytest.approx(20 / 3.6)
    assert merged.server_average_speed_raw == 3
    assert dict(merged.field_sources)["energy_raw"] == (
        "trip_detail" if expected is FieldState.VALID else "travel"
    )
    assert dict(merged.field_provenance)["energy_raw"] == "ec:Wh:maintainer-confirmed"
    empty_summary = parse_ride({}, "202609")
    assert dict(merge_detail(empty_summary, detail).field_states)["energy_raw"] is expected


@pytest.mark.parametrize("raw", [{}, {"trail": None}, {"trail": []}, {"trail": "wrong"}])
def test_missing_null_invalid_trail_does_not_erase_previous_track(raw):
    previous = parse_ride({"trail": "120,30,1,5"}, "202609", source=Endpoint.TRIP_DETAIL)
    detail = parse_ride(raw, "202609", source=Endpoint.TRIP_DETAIL)
    merged = merge_detail(previous, detail)
    assert merged.track_points == previous.track_points
    assert merged.speed_samples == previous.speed_samples
    assert merged.total_track_points == 1
    assert dict(merged.field_provenance)["track_points"].startswith("trail:")
    if not raw or raw.get("trail") is None:
        assert detail.total_track_points is None


def test_verified_empty_track_clears_explicitly_and_partial_track_stays_marked():
    previous = parse_ride({"trail": "120,30,1,5"}, "202609", source=Endpoint.TRIP_DETAIL)
    cleared = merge_detail(
        previous, parse_ride({"trail": ""}, "202609", source=Endpoint.TRIP_DETAIL)
    )
    assert cleared.track_points == cleared.speed_samples == ()
    assert cleared.total_track_points == 0 and not cleared.track_truncated
    assert dict(cleared.field_states)["track_points"] is FieldState.EMPTY
    partial = merge_detail(
        previous, parse_ride({"trail": "bad;121,31,2,6"}, "202609", source=Endpoint.TRIP_DETAIL)
    )
    assert partial.track_points[0].sequence == 1
    assert partial.total_track_points == 2 and "invalid_track_points" in partial.issues


def test_detail_scope_id_conflict_and_combined_timestamps_cannot_cross():
    summary = parse_ride({"travel_id": "a", "end_time": 1790000000}, "202609")
    for detail in (
        replace(summary, query_month="202608"),
        replace(summary, detail_id="other"),
        parse_ride({"travel_id": "a", "id": "other"}, "202609"),
        parse_ride({"start_time": 1790000001}, "202609"),
    ):
        with pytest.raises(NinebotError):
            merge_detail(summary, detail)


def test_partial_time_merge_revalidates_average_and_removes_stale_provenance():
    summary = parse_ride(
        {"start_time": 1790000000, "end_time": 1790000010, "duration": 20, "mileages": 1}, "202609"
    )
    corrected = merge_detail(
        summary, parse_ride({"duration": 10}, "202609", source=Endpoint.TRIP_DETAIL)
    )
    assert "duration_time_difference" not in corrected.issues
    assert dict(corrected.field_sources)["duration_s"] == "trip_detail"
    assert dict(corrected.field_sources)["distance_m"] == "travel"
    manual = replace(
        corrected, duration_s=30, field_provenance=(), field_sources=(), field_states=()
    )
    result = merge_detail(corrected, manual)
    assert "duration_s" not in dict(result.field_provenance)
    assert "duration_time_difference" in result.issues
    missing = parse_ride({}, "202609")
    assert dict(merge_detail(missing, missing).field_states)["track_points"] is FieldState.MISSING


def test_presence_and_provenance_are_charged_to_history_budget():
    from custom_components.ninebot.history import HISTORY_BUDGET, HistoryState, HistoryStore

    ride = parse_ride({"travel_id": "a", "mileages": 1, "duration": 10}, "202609")
    state = HistoryState("entry", "vehicle", "202609", "202609", None, pending=(ride,) * 5000)
    bare = replace(
        state,
        pending=(replace(ride, field_states=(), field_sources=(), field_provenance=()),) * 5000,
    )
    assert bare.retained_cost < HISTORY_BUDGET < state.retained_cost
    assert HistoryStore().put(state, datetime(2026, 9, 26, tzinfo=UTC)) is None
