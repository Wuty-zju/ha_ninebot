"""Offline historical display selections over actual SQLite, not cloud mocks."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ninebot import archive_timeline
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive

NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)
START = NOW - timedelta(days=1)
SN = "synthetic-calendar-vehicle"


def sample(ids=("first", "second"), *, count=None, month="202610", start=START):
    return travel(
        {
            "month": month,
            "times": len(ids) if count is None else count,
            "list": [
                {
                    "travel_id": identity,
                    "start_time": int(start.timestamp()) + index * 3600,
                    "end_time": int(start.timestamp()) + index * 3600 + 600,
                    "mileages": "1.20",
                    "duration": 600,
                    "speed": "30.0",
                    "ec": 100,
                }
                for index, identity in enumerate(ids)
            ],
        },
        month,
    )


@pytest.fixture
async def archive(tmp_path):
    actor = RideArchive(tmp_path / "timeline.sqlite3", "a" * 64)
    await actor.async_open()
    try:
        yield actor
    finally:
        await actor.async_close()


async def test_five_year_timeline_sorted_bounded_read_only_and_scalar(archive):
    await archive.async_record_month(SN, sample(), NOW)
    original = archive.path.read_bytes()
    view = await archive.async_timeline(SN, NOW - timedelta(days=5 * 365), NOW)
    assert [row.ride.ride_id for row in view.records] == ["first", "second"]
    assert view.omitted_invalid_times == 0
    assert view.selection_basis == "latest_lists_and_retained_partial_observations"
    assert all(row.received_at == NOW for row in view.records)
    assert all(not row.ride.track_points and not row.ride.speed_samples for row in view.records)
    assert (await archive.async_latest(SN, NOW)).ride.ride_id == "second"
    assert archive.path.read_bytes() == original
    assert not (await archive.async_timeline("another-vehicle", START, NOW)).records


async def test_exclusive_overlap_cross_midnight_timezone_and_empty_range(archive):
    crossing = datetime(2026, 10, 7, 15, 55, tzinfo=UTC)
    await archive.async_record_month(SN, sample(ids=("crossing",), start=crossing), NOW)
    local = ZoneInfo("Asia/Shanghai")
    view = await archive.async_timeline(
        SN, datetime(2026, 10, 8, tzinfo=local), datetime(2026, 10, 9, tzinfo=local)
    )
    assert len(view.records) == 1  # 23:55 to 00:05 overlaps both days.
    assert not (await archive.async_timeline(SN, crossing + timedelta(minutes=10), NOW)).records
    assert not (await archive.async_timeline(SN, crossing - timedelta(hours=1), crossing)).records


async def test_complete_omissions_hide_current_view_but_partial_omissions_retain(archive):
    await archive.async_record_month(SN, sample(), NOW)
    await archive.async_record_month(
        SN, sample(ids=("first",), count=3), NOW + timedelta(seconds=1)
    )
    view = await archive.async_timeline(SN, START, NOW)
    assert len(view.records) == 2
    await archive.async_record_month(SN, sample(ids=("first",)), NOW + timedelta(seconds=2))
    assert [r.ride.ride_id for r in (await archive.async_timeline(SN, START, NOW)).records] == [
        "first"
    ]
    assert len((await archive.async_page(SN)).rides) == 2  # Evidence is not deleted.
    await archive.async_record_month(SN, sample(ids=()), NOW + timedelta(seconds=3))
    assert not (await archive.async_timeline(SN, START, NOW)).records
    assert await archive.async_latest(SN, NOW) is None
    assert len((await archive.async_page(SN)).rides) == 2


async def test_cross_month_membership_deduplicates_without_false_deletion(archive):
    await archive.async_record_month(SN, sample(ids=("shared",), month="202609"), NOW)
    await archive.async_record_month(SN, sample(ids=("shared",)), NOW + timedelta(seconds=1))
    await archive.async_record_month(SN, sample(ids=()), NOW + timedelta(seconds=2))
    view = await archive.async_timeline(SN, START, NOW)
    assert len(view.records) == 1 and view.records[0].ride.ride_id == "shared"


async def test_latest_reaches_old_months_ignores_future_and_conflicting_times(archive):
    await archive.async_record_month(
        SN, sample(ids=("old",), month="202401", start=datetime(2024, 1, 1, tzinfo=UTC)), NOW
    )
    future = sample(ids=("future",), start=NOW + timedelta(days=1))
    await archive.async_record_month(SN, future, NOW + timedelta(seconds=1))
    conflicting = sample(ids=("conflicting",))
    invalid = replace(conflicting.rides[0], issues=("conflicting_time_representations",))
    conflicting = replace(conflicting, rides=(invalid,))
    await archive.async_record_month(
        SN,
        replace(conflicting, summary=replace(conflicting.summary, list_complete=None)),
        NOW + timedelta(seconds=2),
    )
    assert (await archive.async_latest(SN, NOW)).ride.ride_id == "old"
    view = await archive.async_timeline(SN, START, NOW)
    assert not view.records and view.omitted_invalid_times == 1


async def test_count_and_byte_budgets_fail_explicitly_without_truncating(archive, monkeypatch):
    await archive.async_record_month(SN, sample(), NOW)
    with pytest.raises(ArchiveError) as err:
        await archive.async_timeline(SN, START, NOW, limit=1)
    assert err.value.kind is ArchiveFailure.BUDGET
    monkeypatch.setattr(archive_timeline, "MAX_TIMELINE_BYTES", 1)
    for query in (archive.async_timeline(SN, START, NOW), archive.async_latest(SN, NOW)):
        with pytest.raises(ArchiveError) as err:
            await query
        assert err.value.kind is ArchiveFailure.BUDGET


@pytest.mark.parametrize("limit", [0, True, 5001])
async def test_invalid_query_budget_refused(archive, limit):
    with pytest.raises(ValueError):
        await archive.async_timeline(SN, START, NOW, limit=limit)


@pytest.mark.parametrize(
    "start,end",
    [
        (NOW.replace(tzinfo=None), NOW),
        (NOW, NOW),
        (NOW, START),
        (datetime(2020, 1, 1, tzinfo=UTC), NOW),
    ],
)
async def test_invalid_time_range_refused(archive, start, end):
    with pytest.raises(ValueError):
        await archive.async_timeline(SN, start, end)


async def test_display_budget_is_not_storage_failure_or_write_pause(hass):
    from custom_components.ninebot.archive_runtime import ArchiveStatistics

    store = ArchiveStatistics(hass, "timeline-budget", "synthetic-owner")
    try:
        await store.async_load()
        assert store.writable and store.error is None
        store.record_error(ArchiveError(ArchiveFailure.BUDGET))
        assert store.writable and store.error is None
        await store.async_record(SN, sample(), NOW)
        assert (await store.archive.async_latest(SN, NOW)).ride.ride_id == "second"
    finally:
        await store.async_close()


async def test_archive_change_notifications_are_scoped_content_only_and_unsubscribed(hass):
    from homeassistant.core import callback

    from custom_components.ninebot.archive_runtime import ArchiveStatistics

    store = ArchiveStatistics(hass, "timeline-owner", "synthetic-owner")
    calls = []

    @callback
    def listener():
        assert not store._archive_lock.locked()
        calls.append("changed")

    remove = store.async_add_archive_listener(SN, listener)
    other = store.async_add_archive_listener("another-vehicle", lambda: calls.append("wrong"))
    try:
        await store.async_load()
        await store.async_record(SN, sample(), NOW)
        await hass.async_block_till_done()
        assert calls == ["changed"]
        await store.async_record(SN, sample(), NOW + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert calls == ["changed"]  # A renewed receipt isn't a content revision.
        ride = (await store.archive.async_latest(SN, NOW)).ride
        await store.async_record_detail(
            SN, replace(ride, energy_raw=101), NOW + timedelta(seconds=2), lambda: True
        )
        await hass.async_block_till_done()
        assert calls == ["changed", "changed"]
        remove()
        await store.async_record(SN, sample(ids=()), NOW + timedelta(seconds=3))
        await hass.async_block_till_done()
        assert calls == ["changed", "changed"]
        assert SN not in store._signal(SN)
    finally:
        other()
        await store.async_close()


async def test_guard_loss_or_storage_failure_never_publishes_archive_revision(hass, monkeypatch):
    from unittest.mock import AsyncMock

    from homeassistant.core import callback

    from custom_components.ninebot.archive_runtime import ArchiveStatistics

    store = ArchiveStatistics(hass, "timeline-guard", "synthetic-owner")
    calls = []

    @callback
    def listener():
        calls.append("changed")

    remove = store.async_add_archive_listener(SN, listener)
    try:
        await store.async_load()
        allowed = True
        original = store._project

        async def lose_owner(*args, **kwargs):
            nonlocal allowed
            await original(*args, **kwargs)
            allowed = False

        monkeypatch.setattr(store, "_project", lose_owner)
        await store.async_record(SN, sample(), NOW, guard=lambda: allowed)
        await hass.async_block_till_done()
        assert not calls
        assert (await store.archive.async_latest(SN, NOW)).ride.ride_id == "second"
        monkeypatch.setattr(
            store.archive,
            "async_record_month",
            AsyncMock(side_effect=ArchiveError(ArchiveFailure.STORAGE)),
        )
        await store.async_record(SN, sample(), NOW + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert not calls
    finally:
        remove()
        await store.async_close()
