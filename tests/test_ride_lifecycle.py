"""Recorder-derived synthetic growth replay; no new cloud response is claimed."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.ride_events import SEEN_LIMIT, RideCursor, discover_rides
from custom_components.ninebot.ride_lifecycle import RideLifecycle
from custom_components.ninebot.travel import parse_ride

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def report(distance, duration, energy, *, key="synthetic-ride"):
    return parse_ride(
        {
            "travel_id": key,
            "start_time": int(NOW.timestamp()),
            "end_time": int(NOW.timestamp()) + duration,
            "duration": duration,
            "mileages": distance,
            "ec": energy,
            "speed": 50,
        },
        "202609",
    )


def test_growing_ride_only_emits_after_distinct_unchanged_successes():
    lifecycle = RideLifecycle()
    baseline = report(0.3, 60, 5, key="old")
    cursor = discover_rides(RideCursor(), (baseline,), NOW + timedelta(seconds=60)).cursor
    for distance, duration, energy, minutes in (
        (0.1, 33, 0, 4),
        (4.3, 810, 100, 14),
        (8.7, 1384, 195, 24),
    ):
        ride = report(distance, duration, energy)
        sampled = NOW + timedelta(minutes=minutes)
        lifecycle.observe((ride,), sampled)
        assert not lifecycle.stable_rides((ride,))
        found = discover_rides(cursor, lifecycle.stable_rides((ride,)), sampled)
        assert not found.rides
        cursor = found.cursor
    final = report(8.7, 1384, 195)
    sampled = NOW + timedelta(minutes=34)
    lifecycle.observe((final,), sampled)
    found = discover_rides(cursor, lifecycle.stable_rides((final,)), sampled)
    assert found.rides == (final,)
    assert final.duration_s == 1384 and final.distance_m == 8700
    corrected = replace(final, energy_raw=200)
    lifecycle.observe((corrected,), sampled + timedelta(minutes=10))
    assert lifecycle.phase(corrected) == "revised"
    lifecycle.observe((corrected,), sampled + timedelta(minutes=20))
    assert not discover_rides(
        found.cursor,
        lifecycle.stable_rides((corrected,)),
        sampled + timedelta(minutes=20),
    ).rides


def test_cache_repetition_short_quiet_window_and_older_samples_do_not_settle():
    ride = report(1, 60, 20)
    lifecycle = RideLifecycle()
    first = NOW + timedelta(minutes=5)
    lifecycle.observe((ride,), first)
    for _ in range(3):
        lifecycle.observe((ride,), first)
    lifecycle.observe((ride,), first - timedelta(minutes=1))
    lifecycle.observe((ride,), first + timedelta(minutes=9))
    assert not lifecycle.stable_rides((ride,))
    lifecycle.observe((ride,), first + timedelta(minutes=10))
    assert lifecycle.stable_rides((ride,)) == (ride,)
    assert RideLifecycle().phase(ride) == "reported"  # Restart has no fresh samples.


def test_identity_conflict_invalid_report_and_missing_id_fail_closed():
    ride = report(1, 60, 20)
    lifecycle = RideLifecycle()
    lifecycle.observe((ride,), NOW + timedelta(minutes=2))
    lifecycle.observe((ride, replace(ride, energy_raw=30)), NOW + timedelta(minutes=12))
    assert not lifecycle.stable_rides((ride,))
    lifecycle.observe((replace(ride, ride_id=None),), NOW + timedelta(minutes=22))
    assert not lifecycle.stable_rides((ride,))
    lifecycle.observe((ride,), NOW + timedelta(minutes=32))
    lifecycle.observe((replace(ride, duration_s=0),), NOW + timedelta(minutes=42))
    assert not lifecycle.stable_rides((ride,))


def test_observation_budget_and_cross_month_identity():
    lifecycle = RideLifecycle()
    rides = tuple(report(1, 60, 20, key=f"ride-{n}") for n in range(2 * SEEN_LIMIT))
    first = NOW + timedelta(minutes=2)
    lifecycle.observe(rides, first)
    assert len(lifecycle._observations) == SEEN_LIMIT
    last = rides[-1]
    lifecycle.observe((replace(last, query_month="202610"),), first + timedelta(minutes=10))
    assert lifecycle.stable_rides((last,)) == (last,)
    assert not lifecycle.stable_rides((rides[0],))


@pytest.mark.parametrize("invalid", [float("inf"), float("nan")])
def test_nonfinite_fingerprints_and_invalid_duplicate_never_complete(invalid):
    ride = report(1, 60, 20)
    lifecycle = RideLifecycle()
    first = NOW + timedelta(minutes=2)
    lifecycle.observe((ride,), first)
    broken = replace(ride, energy_raw=invalid)
    lifecycle.observe((broken,), first + timedelta(minutes=10))
    assert lifecycle.phase(broken) == "reported"
    assert not lifecycle.stable_rides((ride,))
    lifecycle.observe((ride,), first + timedelta(minutes=20))
    # An invalid row of the same identity cannot be overridden by a valid row.
    lifecycle.observe((replace(ride, duration_s=0), ride), first + timedelta(minutes=30))
    assert not lifecycle.stable_rides((ride,))
