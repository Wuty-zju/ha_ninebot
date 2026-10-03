import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.exceptions import NinebotError
from custom_components.ninebot.raw import Endpoint
from custom_components.ninebot.ride_models import Ride, SpeedSample
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
