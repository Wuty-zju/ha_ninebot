"""Live report/update semantics without physical-start assumptions or cloud I/O."""

from dataclasses import replace
from datetime import timedelta

from custom_components.ninebot.ride_reports import RideReportCursor

from .test_ride_events import NOW, ride


def test_report_revision_duplicates_partial_omission_and_restart_baseline():
    cursor = RideReportCursor()
    old = ride("old", NOW - timedelta(days=1))
    assert not cursor.observe((old,), NOW, (1, 1))
    new = ride("new", NOW + timedelta(seconds=60))
    assert cursor.observe((old, new, new), NOW + timedelta(seconds=61), (1, 1)) == (
        ("reported", new),
    )
    assert not cursor.observe((old,), NOW + timedelta(seconds=62), (1, 1))
    assert not cursor.observe((new,), NOW + timedelta(seconds=63), (1, 1))
    changed = replace(new, energy_raw=100)
    assert cursor.observe((changed,), NOW + timedelta(seconds=64), (1, 1)) == (
        ("updated", changed),
    )
    assert not cursor.observe((changed,), NOW + timedelta(seconds=64), (1, 1))
    assert not cursor.observe((new,), NOW + timedelta(seconds=65), (2, 1))
    assert not RideReportCursor().observe((new,), NOW + timedelta(seconds=65), (1, 1))


def test_conflicting_missing_future_old_and_power_only_never_create_trip_events():
    cursor = RideReportCursor()
    assert not cursor.observe((), NOW, (1, 1))
    new = ride("new", NOW + timedelta(seconds=60))
    now = NOW + timedelta(seconds=61)
    assert not cursor.observe((new, replace(new, distance_m=500)), now, (1, 1))
    assert not cursor.observe((replace(new, ended_at=None),), now + timedelta(seconds=1), (1, 1))
    assert not cursor.observe(
        (ride("future", now + timedelta(days=1)),), now + timedelta(seconds=2), (1, 1)
    )
    assert not cursor.observe(
        (ride("archive", NOW - timedelta(days=5)),), now + timedelta(seconds=3), (1, 1)
    )
    assert not cursor.observe((new,), now + timedelta(hours=2), (1, 1))
