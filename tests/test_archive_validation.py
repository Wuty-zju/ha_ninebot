"""Untrusted persisted fixtures: reject corrupt facts without repairing in place."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_codec import (
    decoded,
    encoded,
    month_data,
    restored_month,
    restored_profile,
    restored_ride,
    ride_data,
    stamp,
)
from custom_components.ninebot.archive_legacy import legacy_month, prepare_legacy
from custom_components.ninebot.statistics_store import TravelStatisticsStore
from custom_components.ninebot.travel import parse_ride

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def edit(data, path, value):
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


@pytest.mark.parametrize(
    "path,value",
    [
        (("token",), "private"),
        (("source",), "status"),
        (("ride_id",), 123),
        (("detail_id",), ""),
        (("distance_m",), True),
        (("duration_s",), -1),
        (("energy_raw",), float("inf")),
        (("started_at",), "2026-10-08T00:00:00"),
        (("ended_at",), "2026-10-07T00:00:00+00:00"),
        (("query_month",), "202613"),
        (("parser_contract",), 1),
        (("parser_contract",), ""),
        (("issues",), "bad"),
        (("issues",), [42]),
        (("issues",), ["x" * 129]),
        (("precision",), [["duration", True]]),
        (("precision",), [["duration", 16]]),
        (("precision",), [["duration", -1]]),
        (("field_states",), [["duration_s", "invented"]]),
        (("precision",), [["", 1]]),
        (("field_sources",), [["x" * 65, "travel"]]),
        (("field_sources",), [["x", 1]]),
        (("field_provenance",), [["x", "y" * 257]]),
        (("precision",), [["x"]]),
        (("precision",), [["x", 1], ["x", 2]]),
    ],
)
def test_corrupt_ride_facts_rejected(path, value):
    ride = parse_ride(
        {
            "travel_id": "r",
            "start_time": int(NOW.timestamp()),
            "end_time": int(NOW.timestamp()) + 60,
        },
        "202610",
    )
    data = decoded(encoded(ride_data(ride)))
    edit(data, path, value)
    with pytest.raises(ValueError):
        restored_ride(data)


@pytest.mark.parametrize(
    "path,value",
    [
        (("raw",), {}),
        (("summary",), []),
        (("summary", "month"), "202609"),
        (("summary", "daily_mileage"), [{}]),
        (("summary", "daily_mileage", 0, "distance_km"), -1),
        (("summary", "daily_mileage", 0, "day"), "2026-09-01"),
        (("summary", "warnings"), [""]),
        (("summary", "energy_wh"), True),
        (("summary", "returned_count"), None),
        (("summary", "unique_ride_count"), 2),
        (("summary", "ride_count"), -1),
        (("summary", "list_complete"), 1),
        (("summary", "chart_status"), "guess"),
        (("summary", "chart_status"), "invalid"),
        (("precision",), "bad"),
        (("precision",), [["", 1]]),
        (("precision",), [["x", 16]]),
        (("precision",), [["x", 1], ["x", 2]]),
    ],
)
def test_corrupt_calendar_summary_rejected(path, value):
    data = decoded(
        encoded(
            month_data(
                travel(
                    {
                        "times": 0,
                        "list": [],
                        "total_mileages": 1,
                        "detail": [1] + [0] * 30,
                    },
                    "202610",
                )
            )
        )
    )
    edit(data, path, value)
    with pytest.raises(ValueError):
        restored_month(data)


@pytest.mark.parametrize("value", [None, 1, "2026-10-08", "1999-01-01T00:00:00+00:00"])
def test_invalid_timestamp_cannot_be_restored(value):
    with pytest.raises(ValueError):
        stamp(value)


def test_row_budget_and_profile_whitelist():
    with pytest.raises(ValueError):
        encoded({"x": "large" * 30000})
    for data in ("[1]", '"' + "x" * 131073 + '"'):
        with pytest.raises(ValueError):
            decoded(data)
    for profile in (
        {"sn": "s", "model": "m", "name": "n", "account": "secret"},
        {"sn": "s", "model": "m", "name": "\nprivate"},
        {"sn": "s", "model": "m", "name": ""},
    ):
        original = json.dumps(profile)
        with pytest.raises(ValueError):
            restored_profile(profile)
        assert json.dumps(profile) == original


def test_legacy_source_rejection_is_atomic_and_does_not_overwrite_good_view(hass):
    source = TravelStatisticsStore(hass, "legacy-validation")
    source.update(
        "s",
        travel(
            {
                "times": 1,
                "list": [
                    {
                        "travel_id": "r",
                        "start_time": int(NOW.timestamp()),
                        "end_time": int(NOW.timestamp()) + 60,
                        "duration": 60,
                    }
                ],
            },
            "202610",
        ),
        NOW,
    )
    original = json.loads(json.dumps(source.dump()))
    key = source.vehicle_key("s")
    month = ("months", key, "202610")
    ride = ("rides", key, "r")
    corruptions = [
        (("schema_version",), True),
        (("months",), []),
        (("months", "private"), {}),
        (month, {}),
        ((*month, "month"), "202609"),
        ((*month, "revision"), 0),
        ((*month, "ride_count"), True),
        ((*month, "backend_version"), "unknown secret"),
        ((*month, "list_complete"), 1),
        ((*month, "returned_ids"), ["r", "r"]),
        ((*month, "daily_distance_km"), {}),
        ((*month, "chart_status"), "guess"),
        ((*month, "chart_status"), "valid"),
        ((*month, "daily_distance_km"), [None]),
        ((*month, "returned_ids"), [True]),
        ((*month, "energy_wh"), -1),
        ((*ride, "ride_id"), "other"),
        ((*ride, "started_at"), "2026-10-08"),
        ((*ride, "ended_at"), "2026-10-07T00:00:00+00:00"),
        ((*ride, "distance_m"), True),
        ((*ride, "current_fields"), "unknown"),
        ((*ride, "current_fields"), ["private"]),
    ]
    for path, value in corruptions:
        data = json.loads(json.dumps(original))
        edit(data, path, value)
        before = json.dumps(data, sort_keys=True)
        with pytest.raises((ValueError, TypeError, KeyError)):
            source.restore(data)
        assert json.dumps(data, sort_keys=True) == before
        assert json.loads(json.dumps(source.dump())) == original


def test_legacy_import_rejects_bad_scope_membership_and_budget(hass, monkeypatch):
    import custom_components.ninebot.archive_legacy as module

    store = TravelStatisticsStore(hass, "legacy-budget")
    store.update("s", travel({"times": 1, "list": [{"travel_id": "r"}]}, "202610"), NOW)
    key = store.vehicle_key("s")
    month = store.month("s", "202610")
    ride = store.rides[key]["r"]
    for months, rides in (
        ({"private": {}}, {}),
        ({key: {"202609": month}}, {}),
        ({}, {key: {"wrong": ride}}),
    ):
        with pytest.raises(ValueError):
            prepare_legacy(months, rides)
    with pytest.raises(ValueError):
        legacy_month(replace(month, daily_distance_km=(1,)))
    truncated = legacy_month(replace(month, ride_count=2, list_complete=True))
    assert truncated.summary.list_complete is False
    assert "legacy_membership_truncated" in truncated.summary.warnings
    monkeypatch.setattr(module, "MAX_MONTHS", 0)
    with pytest.raises(ValueError):
        prepare_legacy(store.months, store.rides)
