"""Synthetic scoped aggregation boundaries; no new recorded or cloud evidence."""

import json
from dataclasses import replace

import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.history import HistoryState
from custom_components.ninebot.history_actions import merge_month
from custom_components.ninebot.services import month_response, ride_response
from custom_components.ninebot.travel import parse_ride
from custom_components.ninebot.travel_statistics import energy_statistics, returned_statistics


def test_server_summary_returned_subset_and_single_ride_keep_distinct_bases():
    travel = adapters.travel(
        {
            "times": 128,
            "ec": 1000,
            "total_mileages": 100,
            "list": [
                {"travel_id": "a", "mileages": 1, "ec": 30},
                {"travel_id": "b", "mileages": 3, "ec": 30},
            ],
        },
        "202609",
    )
    result = month_response(travel)
    server = result["statistics"]["server_month"]
    subset = result["statistics"]["returned_rides"]
    assert server["energy_intensity_wh_per_km"] == 10
    assert subset["energy_intensity_wh_per_km"] == 15  # weighted 60/4, not mean(30,10)
    assert server["basis"] == "server_month_summary"
    assert subset["basis"] == "returned_unique_rides"
    assert result["coverage"]["fraction"] == 2 / 128
    assert result["coverage"]["list_complete"] is False
    assert ride_response(travel.rides[0])["energy_intensity_wh_per_km"] == 30
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "distance,energy",
    [
        (0, 0),
        (0, 1),
        (None, 1),
        (1, None),
        (-1, 1),
        (1, -1),
        (float("nan"), 1),
        (1, float("inf")),
        (1e-300, 1e300),
    ],
)
def test_invalid_missing_zero_or_overflow_intensity_is_unknown(distance, energy):
    result = energy_statistics(distance, energy, basis="test")
    assert result["energy_intensity_wh_per_km"] is None
    json.dumps(result, allow_nan=False)


def test_duplicates_are_deduplicated_missing_and_conflicting_identity_not_invented():
    a = parse_ride({"travel_id": "a", "mileages": 1, "ec": 20}, "202609")
    b = parse_ride({"travel_id": "b", "mileages": 3, "ec": 30}, "202609")
    valid = returned_statistics((a, a, b))
    assert valid["unique_ride_count"] == 2 and valid["observed_row_count"] == 3
    assert valid["energy_intensity_wh_per_km"] == 12.5
    for rows in ((a, replace(a, energy_raw=99)), (a, replace(b, ride_id=None))):
        result = returned_statistics(rows)
        assert result["identity_complete"] is False
        assert result["energy_intensity_wh_per_km"] is None
        assert result["distance_km"] is result["energy_wh"] is None
    incomplete = returned_statistics((a, replace(b, energy_raw=None)))
    assert incomplete["distance_km"] == 4 and incomplete["energy_wh"] is None
    assert incomplete["metrics_complete"] is False
    empty = returned_statistics(())
    assert empty["distance_km"] == empty["energy_wh"] == 0
    assert empty["energy_intensity_wh_per_km"] is None


def test_cross_month_index_accumulates_unique_metrics_and_marks_conflicts():
    first = adapters.travel(
        {
            "total_mileages": 100,
            "ec": 1000,
            "times": 5,
            "list": [{"travel_id": "a", "mileages": 1, "ec": 20}],
        },
        "202609",
    )
    second = adapters.travel(
        {
            "total_mileages": 200,
            "ec": 1000,
            "times": 6,
            "list": [
                {"travel_id": "a", "mileages": 1, "ec": 20},
                {"travel_id": "b", "mileages": 3, "ec": 30},
            ],
        },
        "202608",
    )
    state = HistoryState("entry", "vehicle", "202608", "202609", "202609")
    state = merge_month(merge_month(state, first), second)
    assert state.totals[:2] == (300, 2000)
    assert state.indexed_totals == (4000, 50)
    assert state.indexed_identity_complete and len(state.seen) == 2
    conflicting = replace(second, rides=(replace(second.rides[0], energy_raw=99),))
    assert not merge_month(state, conflicting).indexed_identity_complete
    missing_id = replace(second, rides=(replace(second.rides[0], ride_id=None),))
    assert not merge_month(state, missing_id).indexed_identity_complete
    missing_metric = replace(
        second, rides=(replace(second.rides[1], ride_id="c", energy_raw=None),)
    )
    assert merge_month(state, missing_metric).indexed_totals[1] is None
