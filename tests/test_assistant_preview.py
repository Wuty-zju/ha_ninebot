"""Bounded detail previews over real synthetic archives, without cloud or writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.preservation import scope_fingerprint
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import ArchiveError, RideArchive

NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)
START = NOW - timedelta(days=1)
SN = "SyntheticSN"


def sample(count=2, *, month="202610", start=START):
    return travel(
        {
            "times": count,
            "list": [
                {
                    "travel_id": f"ride-{i:03}",
                    "start_time": int(start.timestamp()) + i * 1000,
                    "end_time": int(start.timestamp()) + i * 1000 + 600,
                    "duration": 600,
                    "mileages": 1.2,
                }
                for i in range(count)
            ],
        },
        month,
    )


@pytest.fixture
async def archive(tmp_path):
    actor = RideArchive(tmp_path / "preview.sqlite3", "a" * 64)
    await actor.async_open()
    try:
        yield actor
    finally:
        await actor.async_close()


async def preview(archive, *, sn=SN, start=START, end=NOW):
    return await archive.async_detail_preview(sn, start, end, NOW, guard=lambda: True)


async def test_unobserved_empty_is_distinct_from_recorded_complete_empty(archive):
    before = archive.path.read_bytes()
    unseen = await preview(archive)
    assert unseen.known_count == 0 and not unseen.months and unseen.scope is None
    assert archive.path.read_bytes() == before
    await archive.async_record_month(SN, sample(0), NOW)
    empty = await preview(archive)
    assert empty.known_count == 0 and len(empty.months) == 1
    assert empty.months[0].list_complete is True
    assert empty.months[0].reported_count == 0


async def test_saved_details_do_not_hide_later_candidates_and_preview_never_writes(archive):
    month = sample(45)
    await archive.async_record_month(SN, month, NOW)
    receipt = NOW + timedelta(seconds=1)
    for ride in month.rides[:20]:
        await archive.async_record_detail(
            SN, ride, receipt, raw=build_record(Endpoint.TRIP_DETAIL, {}, receipt, "202610")
        )
    before = archive.path.read_bytes()
    result = await preview(archive)
    assert result.known_count == 45
    assert result.saved_count == 20 and result.missing_count == 25
    assert result.unmapped_count == 0 and result.has_more
    assert result.scope is not None and result.selected_count == 20
    assert result.scope.rides == tuple(
        (ride.ride_id, scope_fingerprint(ride)) for ride in month.rides[20:40]
    )
    assert archive.path.read_bytes() == before
    assert not (await preview(archive, sn="another-vehicle")).scope
    assert not (await preview(archive, sn="another-vehicle")).months


async def test_unmapped_and_conflicting_identities_are_not_executable(archive):
    month = sample(3)
    month = replace(
        month,
        rides=(
            replace(month.rides[0], detail_id=None),
            replace(month.rides[1], issues=("conflicting_detail_ids",)),
            month.rides[2],
        ),
    )
    await archive.async_record_month(SN, month, NOW)
    result = await preview(archive)
    assert result.known_count == 3 and result.unmapped_count == 2
    assert result.missing_count == result.selected_count == 1
    assert result.scope.rides[0][0] == month.rides[2].ride_id


async def test_partial_omission_is_retained_complete_omission_is_excluded(archive):
    original = sample(3)
    await archive.async_record_month(SN, original, NOW)
    partial = travel({"times": 3, "list": []}, "202610")
    await archive.async_record_month(SN, partial, NOW + timedelta(seconds=1))
    assert (await preview(archive)).known_count == 3
    await archive.async_record_month(SN, sample(0), NOW + timedelta(seconds=2))
    assert (await preview(archive)).known_count == 0
    assert len((await archive.async_page(SN)).rides) == 3


async def test_overlap_across_midnight_and_month_uses_recorded_history_contract(archive):
    # A ride straddles business midnight: include its full identity, never split it.
    start = datetime(2026, 9, 30, 15, 55, tzinfo=UTC)
    await archive.async_record_month(SN, sample(1, month="202609", start=start), NOW)
    result = await preview(
        archive,
        start=datetime(2026, 9, 30, 16, tzinfo=UTC),
        end=datetime(2026, 10, 1, 16, tzinfo=UTC),
    )
    assert result.known_count == 1 and result.scope is not None
    # No October list observed; one local overlap does not prove full coverage.
    assert not result.months


async def test_future_ongoing_and_conflicting_times_are_not_approved(archive):
    month = sample(4)
    month = replace(
        month,
        rides=(
            month.rides[0],
            replace(month.rides[1], ended_at=NOW + timedelta(seconds=1)),
            replace(
                month.rides[2],
                started_at=NOW + timedelta(seconds=1),
                ended_at=NOW + timedelta(seconds=2),
            ),
            replace(month.rides[3], issues=("conflicting_time_representations",)),
        ),
    )
    await archive.async_record_month(SN, month, NOW)
    result = await preview(archive, end=NOW + timedelta(days=1))
    assert result.known_count == result.selected_count == 1
    assert result.skipped_conflicting_times == 1


@pytest.mark.parametrize("days", [0, -1, 32])
async def test_invalid_range_is_rejected(archive, days):
    with pytest.raises(ValueError):
        await preview(archive, end=START + timedelta(days=days))


async def test_guard_is_required_before_archive_read(archive):
    with pytest.raises(ArchiveError):
        await archive.async_detail_preview(SN, START, NOW, NOW, guard=lambda: False)


async def test_scan_budget_fails_instead_of_approving_a_silent_subset(archive):
    from unittest.mock import patch

    from custom_components.ninebot.ride_archive import ArchiveFailure

    await archive.async_record_month(SN, sample(3), NOW)
    with patch("custom_components.ninebot.archive_browse.MAX_TIMELINE_RIDES", 2):
        with pytest.raises(ArchiveError) as error:
            await preview(archive)
    assert error.value.kind is ArchiveFailure.BUDGET


async def test_stored_request_month_must_still_have_a_current_mapping(archive):
    await archive.async_record_month(SN, sample(1, month="202609"), NOW)
    await archive.async_record_month(SN, sample(1), NOW + timedelta(seconds=1))
    before = await preview(archive)
    assert before.scope is not None
    await archive.async_record_month(SN, sample(0), NOW + timedelta(seconds=2))
    # September still keeps the ride visible; its stored October mapping is now omitted.
    after = await preview(archive)
    assert after.known_count == after.unmapped_count == 1 and after.scope is None
    changes = await archive._run(lambda: archive._db().total_changes)
    assert (
        await archive.async_preservation_claim(
            SN, NOW + timedelta(days=1), 60, scope=before.scope, guard=lambda: True
        )
        is None
    )
    assert await archive._run(lambda: archive._db().total_changes) == changes
