"""Event cursor safety without HA state, disk writes or cloud polling."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.raw import Endpoint
from custom_components.ninebot.ride_events import (
    SEEN_LIMIT,
    InvalidRideCursor,
    RideCursor,
    completed_report,
    discover_rides,
)
from custom_components.ninebot.ride_models import Ride

NOW = datetime(2026, 9, 30, 15, 59, tzinfo=UTC)


def ride(key, end, month="202609"):
    return Ride(
        month,
        Endpoint.TRAVEL,
        ride_id=key,
        detail_id=key,
        started_at=end - timedelta(seconds=60),
        ended_at=end,
        distance_m=300,
        duration_s=60,
        server_max_speed_m_s=10,
        field_provenance=(("ride_id", "travel_id"),),
    )


def test_startup_empty_baseline_and_new_report_without_historical_replay():
    initial = RideCursor()
    empty = discover_rides(initial, (), NOW)
    assert empty.reason == "awaiting_baseline" and empty.cursor.baseline_at is None
    old = ride("older", NOW - timedelta(days=2))
    baseline = discover_rides(empty.cursor, (old,), NOW)
    assert not baseline.rides and baseline.reason == "baseline"
    assert baseline.cursor.baseline_at == NOW
    newer = ride("new", NOW + timedelta(minutes=5))
    discovered = discover_rides(baseline.cursor, (newer, old), NOW + timedelta(minutes=6))
    assert discovered.rides == (newer,)
    assert not discover_rides(discovered.cursor, (old, newer), NOW + timedelta(minutes=7)).rides
    assert RideCursor.restore(discovered.cursor.dump()) == discovered.cursor
    assert "older" not in str(discovered.cursor.dump())


def test_reordering_duplicates_cross_month_and_late_arrivals():
    cursor = discover_rides(RideCursor(), (ride("baseline", NOW),), NOW).cursor
    first = ride("first", NOW + timedelta(minutes=5), "202610")
    later = discover_rides(cursor, (first,), NOW + timedelta(minutes=10))
    assert later.rides == (first,)
    duplicate_month = replace(first, query_month="202609")
    assert not discover_rides(
        later.cursor, (duplicate_month, first), NOW + timedelta(minutes=11)
    ).rides
    late = ride("late", NOW + timedelta(minutes=3), "202610")
    updated = discover_rides(later.cursor, (first, late), NOW + timedelta(minutes=15))
    assert updated.rides == (late,)
    assert not discover_rides(updated.cursor, (late, first), NOW + timedelta(minutes=16)).rides
    backfill = ride("backfill", NOW - timedelta(seconds=1))
    expired = ride("expired", NOW + timedelta(minutes=1))
    assert not discover_rides(
        updated.cursor, (backfill, expired), NOW + timedelta(minutes=40)
    ).rides


def test_restart_backup_clock_regression_and_long_gap_rebaseline():
    base = discover_rides(RideCursor(), (ride("old", NOW),), NOW).cursor
    new = ride("new", NOW + timedelta(minutes=5))
    for timestamp, force in (
        (NOW + timedelta(minutes=6), True),
        (NOW + timedelta(days=2), False),
        (NOW - timedelta(seconds=1), False),
    ):
        updated = discover_rides(base, (new,), timestamp, baseline=force)
        assert not updated.rides
    # An old restored backup is not a reason to replay trips present on startup.
    restored = RideCursor.restore(base.dump())
    restart = discover_rides(restored, (new,), NOW + timedelta(minutes=6), baseline=True)
    assert not restart.rides and restart.cursor.baseline_at == NOW + timedelta(minutes=6)


def test_unknown_future_inconsistent_or_conflicting_completion_is_not_emitted():
    candidate = ride("known", NOW)
    assert completed_report(candidate, NOW)
    for bad in (
        replace(candidate, ride_id=None),
        replace(candidate, started_at=None),
        replace(candidate, ended_at=NOW + timedelta(seconds=1)),
        replace(candidate, duration_s=0),
        replace(candidate, duration_s=2),
        replace(candidate, issues=("conflicting_time_representations",)),
        replace(candidate, issues=("conflicting_detail_ids",)),
        replace(candidate, started_at=NOW + timedelta(seconds=1)),
        replace(candidate, ride_id="unsafe\n"),
        replace(candidate, field_provenance=(("ride_id", "legacy-id"),)),
    ):
        assert not completed_report(bad, NOW)
    cursor = discover_rides(RideCursor(), (candidate,), NOW).cursor
    one = ride("same-id", NOW + timedelta(minutes=5))
    different = ride("same-id", NOW + timedelta(minutes=6))
    assert not discover_rides(cursor, (one, different), NOW + timedelta(minutes=7)).rides


def test_capacity_floor_prevents_replay_even_after_ids_are_evicted():
    cursor = discover_rides(RideCursor(), (ride("base", NOW),), NOW).cursor
    batch = tuple(ride(f"id-{index}", NOW + timedelta(seconds=index + 1)) for index in range(200))
    updated = discover_rides(cursor, batch, NOW + timedelta(minutes=4))
    assert len(updated.cursor.seen) <= SEEN_LIMIT
    assert len(updated.rides) <= SEEN_LIMIT
    assert updated.cursor.retention_floor is not None
    replay = discover_rides(updated.cursor, tuple(reversed(batch)), NOW + timedelta(minutes=5))
    assert not replay.rides
    assert RideCursor.restore(replay.cursor.dump()) == replay.cursor
    # A tie batch larger than capacity cannot be durably distinguished, so all
    # tied candidates are conservatively suppressed rather than replayed later.
    tied = tuple(ride(f"tie-{index}", NOW + timedelta(minutes=8)) for index in range(200))
    overflow = discover_rides(replay.cursor, tied, NOW + timedelta(minutes=9))
    assert not overflow.rides
    assert not discover_rides(overflow.cursor, tied, NOW + timedelta(minutes=10)).rides


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: None,
        lambda raw: {},
        lambda raw: {**raw, "seen": {}},
        lambda raw: {**raw, "seen": [["invalid", NOW.isoformat()]]},
        lambda raw: {**raw, "baseline_at": "not-a-date"},
        lambda raw: {**raw, "baseline_at": "2026-01-01T00:00:00"},
        lambda raw: {**raw, "baseline_at": 12},
        lambda raw: {**raw, "observed_at": None},
        lambda raw: {**raw, "baseline_at": None},
        lambda raw: {**raw, "retention_floor": (NOW + timedelta(days=1)).isoformat()},
        lambda raw: {**raw, "seen": raw["seen"] * 2},
        lambda raw: {**raw, "seen": raw["seen"] * 129},
        lambda raw: {**raw, "seen": [[raw["seen"][0][0], None]]},
        lambda raw: {**raw, "seen": [[raw["seen"][0][0], (NOW + timedelta(days=1)).isoformat()]]},
        lambda raw: {**raw, "seen": ["invalid"]},
    ],
)
def test_invalid_persistent_cursor_is_not_silently_accepted(mutate):
    raw = discover_rides(RideCursor(), (ride("baseline", NOW),), NOW).cursor.dump()
    with pytest.raises(InvalidRideCursor):
        RideCursor.restore(mutate(raw))
