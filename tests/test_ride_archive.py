"""Actual offline SQLite transactions, scope, coverage, restart and backup."""

import asyncio
import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_codec import (
    decoded,
    encoded,
    merged_ride,
    month_data,
    restored_month,
    restored_ride,
    ride_data,
)
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.raw import Endpoint
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive
from custom_components.ninebot.ride_models import FieldState
from custom_components.ninebot.statistics_store import TravelStatisticsStore
from custom_components.ninebot.travel import parse_ride

NOW = datetime(2026, 9, 26, tzinfo=UTC)
OWNER = hashlib.sha256(b"synthetic-account").hexdigest()
SN = "synthetic-vehicle"


def sample(month="202609", *, ids=("first", "second"), count=None, distance="1.20"):
    return travel(
        {
            "month": month,
            "times": len(ids) if count is None else count,
            "total_mileages": float(distance) * len(ids),
            "ec": 120 * len(ids),
            "duration": 600 * len(ids),
            "list": [
                {
                    "travel_id": identity,
                    "mileages": distance,
                    "ec": 120,
                    "duration": 600,
                    "speed": "30.0",
                    "start_time": int(NOW.timestamp()) + index * 1000,
                    "end_time": int(NOW.timestamp()) + index * 1000 + 600,
                }
                for index, identity in enumerate(ids)
            ],
        },
        month,
    )


@pytest.fixture
async def archive(tmp_path):
    store = RideArchive(tmp_path / "account" / "rides.sqlite3", OWNER)
    await store.async_open()
    yield store
    await store.async_close()


def test_codec_preserves_precision_sources_but_excludes_track_and_naive_time():
    detail = parse_ride(
        {
            "travel_id": "first",
            "mileages": "1.20",
            "speed": 30,
            "trail": "120,30,4,2;120.1,30.1,3,5",
        },
        "202609",
        source=Endpoint.TRIP_DETAIL,
    )
    restored = restored_ride(decoded(encoded(ride_data(detail))))
    assert restored.detail_id == "first" and dict(restored.precision)["mileages"] == 2
    assert restored.server_max_speed_m_s == pytest.approx(30 / 3.6)
    assert not restored.track_points and not restored.speed_samples
    assert dict(restored.field_provenance)["distance_m"].startswith("mileages:km:")
    assert "latitude" not in encoded(ride_data(detail))
    with pytest.raises(ValueError):
        ride_data(replace(detail, started_at=NOW.replace(tzinfo=None)))


@pytest.mark.parametrize(
    "fault",
    [
        "warnings_string",
        "coverage",
        "duplicate_day",
        "wrong_total",
        "precision_shape",
        "complete_count",
    ],
)
def test_codec_rejects_invalid_calendar_metadata_and_completeness(fault):
    raw = {
        "month": "202609",
        "total_mileages": 1.2,
        "times": 0,
        "list": [],
        "detail": [1.2] + [0] * 29,
    }
    data = decoded(encoded(month_data(travel(raw, "202609"))))
    if fault == "warnings_string":
        data["summary"]["warnings"] = "private value"
    elif fault == "coverage":
        data["summary"]["coverage"] = 1.1
    elif fault == "duplicate_day":
        data["summary"]["daily_mileage"][1]["day"] = "2026-09-01"
    elif fault == "wrong_total":
        data["summary"]["mileage_km"] = 100
    elif fault == "precision_shape":
        data["precision"] = [["x"]]
    else:
        data["summary"]["ride_count"] = 1
    with pytest.raises(ValueError):
        restored_month(data)


def test_patch_retains_old_valid_values_and_marks_current_missing_and_conflict():
    old = sample().rides[0]
    incoming = parse_ride({"travel_id": "first", "duration": 700}, "202609")
    merged = merged_ride(old, incoming)
    assert merged.distance_m == 1200 and merged.duration_s == 700
    assert dict(merged.field_states)["distance_m"] == FieldState.MISSING
    assert dict(merged.field_sources)["distance_m"] == "travel"
    assert dict(merged.precision)["mileages"] == 2
    conflict = merged_ride(old, replace(incoming, detail_id="other"))
    assert conflict.detail_id is None
    assert merged_ride(conflict, old).detail_id is None


async def test_month_revision_restart_partial_dedup_and_old_reply(archive):
    assert await archive.async_record_month(SN, sample(), NOW, "0.1.7")
    page = await archive.async_page(SN)
    assert not await archive.async_record_month(SN, sample(), NOW + timedelta(seconds=1), "0.1.7")
    assert (await archive.async_page(SN)).revision == page.revision
    assert not await archive.async_record_month(SN, sample(distance=99), NOW - timedelta(seconds=1))
    assert (await archive.async_month(SN, "202609")).travel.mileage == 2.4
    await archive.async_record_month(
        SN, sample(ids=("first",), count=2), NOW + timedelta(seconds=2)
    )
    result = await archive.async_month(SN, "202609")
    assert result.known_ride_count == 2 and not result.complete
    assert len(result.travel.rides) == 1 and len((await archive.async_page(SN)).rides) == 2
    # Later scalar detail supplements the same stable identity, not a new ride.
    detail = replace(sample().rides[0], source=Endpoint.TRIP_DETAIL, energy_raw=130)
    await archive.async_record_detail(SN, detail, NOW + timedelta(seconds=3))
    assert (await archive.async_month(SN, "202609")).travel.rides[0].energy_raw == 130
    path = archive.path
    await archive.async_close()
    restored = RideArchive(path, OWNER)
    try:
        await restored.async_open()
        assert len((await restored.async_page(SN)).rides) == 2
        assert (await restored.async_month(SN, "202609")).known_ride_count == 2
    finally:
        await restored.async_close()


async def test_profile_scope_retirement_signed_image_exclusion_and_detail_owner(archive):
    profile = VehicleProfile(
        SN, "Test vehicle", "Test model", "https://invalid/?token=secret", {"vin": "private"}
    )
    await archive.async_record_profiles((profile,), NOW, complete=True)
    assert await archive.async_profiles() == (VehicleProfile(SN, "Test vehicle", "Test model"),)
    await archive.async_record_month(SN, sample(), NOW)
    assert not (await archive.async_page("other")).rides
    with pytest.raises(ArchiveError) as err:
        await archive.async_record_detail("other", sample().rides[0], NOW)
    assert err.value.kind == ArchiveFailure.OWNER
    await archive.async_record_profiles((), NOW + timedelta(seconds=1), complete=True)
    assert await archive.async_profiles() == ()
    # Retirement hides local discovery; historical facts are not erased.
    assert len((await archive.async_page(SN)).rides) == 2
    await archive.async_record_profiles((profile,), NOW, complete=True)
    assert await archive.async_profiles() == ()


async def test_keyset_pages_revision_vehicle_and_range_fences(archive):
    await archive.async_record_month(SN, sample(ids=("1", "2", "3")), NOW)
    first = await archive.async_page(SN, limit=1)
    second = await archive.async_page(SN, limit=1, cursor=first.next_cursor)
    assert first.rides[0].ride_id == "1" and second.rides[0].ride_id == "2"
    for sn, start, end in [
        ("other", None, None),
        (SN, NOW - timedelta(days=1), NOW + timedelta(days=1)),
    ]:
        with pytest.raises(ArchiveError) as err:
            await archive.async_page(sn, start=start, end=end, cursor=first.next_cursor)
        assert err.value.kind == ArchiveFailure.CURSOR
    ranged = await archive.async_page(
        SN, start=NOW + timedelta(seconds=600), end=NOW + timedelta(seconds=1600)
    )
    assert [ride.ride_id for ride in ranged.rides] == ["2"]
    await archive.async_record_month(
        SN, sample(ids=("1", "2", "3"), distance="1.3"), NOW + timedelta(seconds=1)
    )
    with pytest.raises(ArchiveError) as err:
        await archive.async_page(SN, cursor=first.next_cursor)
    assert err.value.kind == ArchiveFailure.CURSOR


async def test_backup_restore_and_future_owner_corrupt_files_untouched(archive, tmp_path):
    await archive.async_record_month(SN, sample(), NOW)
    backup = tmp_path / "backup.sqlite3"
    await archive.async_backup(backup)
    assert backup.stat().st_mode & 0o777 == 0o600
    recovered = RideArchive(backup, OWNER)
    await recovered.async_open()
    assert len((await recovered.async_page(SN)).rides) == 2
    await recovered.async_close()
    with pytest.raises(ArchiveError):
        await archive.async_backup(backup)
    for fault in ("owner", "future", "corrupt"):
        path = tmp_path / (fault + ".sqlite3")
        await archive.async_backup(path)
        if fault == "future":
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA user_version=99")
        elif fault == "corrupt":
            path.write_bytes(b"not a SQLite archive")
        before = path.read_bytes()
        bad = RideArchive(path, "f" * 64 if fault == "owner" else OWNER)
        try:
            with pytest.raises(ArchiveError):
                await bad.async_open()
            assert path.read_bytes() == before
        finally:
            await bad.async_close()


async def test_capacity_rolls_back_whole_batch_preserves_old_rows_and_allows_reads(tmp_path):
    store = RideArchive(tmp_path / "limited.sqlite3", OWNER, max_bytes=128 * 1024)
    try:
        await store.async_open()
        await store.async_record_month(SN, sample(), NOW)
        revision = (await store.async_page(SN)).revision
        with pytest.raises(ArchiveError) as err:
            await store.async_record_month(
                SN,
                sample("202608", ids=tuple(f"id-{i}" for i in range(200))),
                NOW + timedelta(seconds=1),
            )
        assert err.value.kind == ArchiveFailure.CAPACITY and store.write_paused
        assert await store.async_month(SN, "202608") is None
        assert len((await store.async_page(SN)).rides) == 2
        assert (await store.async_page(SN)).revision == revision
        with pytest.raises(ArchiveError):
            await store.async_record_month(SN, sample(), NOW + timedelta(seconds=2))
    finally:
        await store.async_close()


async def test_cancel_and_close_wait_for_worker_transaction_boundary(archive):
    loop = asyncio.get_running_loop()
    begun = asyncio.Event()
    release = threading.Event()

    def blocked():
        loop.call_soon_threadsafe(begun.set)
        assert release.wait(5)
        return True

    task = asyncio.create_task(archive._run(blocked))
    await begun.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and archive._pending == 1
    close = asyncio.create_task(archive.async_close())
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await close
    assert archive._pending == 0 and archive._connection is None
    with pytest.raises(ArchiveError):
        await archive.async_page(SN)


async def test_legacy_import_atomic_once_missing_ids_explicit_and_original_unchanged(archive, hass):
    legacy = TravelStatisticsStore(hass, "entry")
    legacy.update(SN, sample(), NOW)
    key = legacy.vehicle_key(SN)
    del legacy.rides[key]["second"]  # Historical eviction must not appear complete.
    before = json.dumps(legacy.dump(), sort_keys=True)
    assert await archive.async_import_legacy(legacy.months, legacy.rides)
    assert not await archive.async_import_legacy(legacy.months, legacy.rides)
    assert json.dumps(legacy.dump(), sort_keys=True) == before
    month = await archive.async_month(SN, "202609")
    assert month.missing_ids == ("second",) and not month.complete
    assert month.travel.mileage == 2.4 and month.travel.reported_ride_count == 2
    ride = month.travel.rides[0]
    assert ride.detail_id is None and ride.server_max_speed_m_s is None
    assert "legacy_precision_unavailable" in ride.issues
    assert not ride.precision


async def test_invalid_legacy_import_not_marked_or_partially_written(archive, hass):
    legacy = TravelStatisticsStore(hass, "entry")
    legacy.update(SN, sample(), NOW)
    key = legacy.vehicle_key(SN)
    row = legacy.months[key]["202609"]
    legacy.months[key]["202609"] = replace(row, daily_distance_km=(1,), chart_status="valid")
    with pytest.raises(ArchiveError):
        await archive.async_import_legacy(legacy.months, legacy.rides)
    assert not (await archive.async_page(SN)).rides
    legacy.months[key]["202609"] = row
    assert await archive.async_import_legacy(legacy.months, legacy.rides)


async def test_ten_thousand_rides_indexed_bounded_pages_without_history_eviction(
    archive, record_property
):
    started = perf_counter()
    for month_number in range(1, 11):
        month = f"2026{month_number:02d}"
        await archive.async_record_month(
            SN,
            sample(month, ids=tuple(f"{month}-{index:04d}" for index in range(1000))),
            NOW + timedelta(seconds=month_number),
        )
    cursor = None
    ids = set()
    while True:
        page = await archive.async_page(SN, limit=100, cursor=cursor)
        assert len(page.rides) <= 100
        ids.update(ride.ride_id for ride in page.rides)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(ids) == 10000
    assert len((await archive.async_month(SN, "202601")).travel.rides) == 1000
    record_property("archive_10000_seconds", round(perf_counter() - started, 3))


async def test_queued_commit_guard_runs_on_loop_and_prevents_stale_owner_write(archive):
    loop = asyncio.get_running_loop()
    begun = asyncio.Event()
    release = threading.Event()
    owned = True
    caller_thread = threading.get_ident()
    observed_threads = []

    def guard():
        observed_threads.append(threading.get_ident())
        return owned

    def blocked():
        loop.call_soon_threadsafe(begun.set)
        assert release.wait(5)

    first = asyncio.create_task(archive._run(blocked))
    await begun.wait()
    queued = asyncio.create_task(archive.async_record_month(SN, sample(), NOW, guard=guard))
    await asyncio.sleep(0)
    owned = False
    release.set()
    await first
    with pytest.raises(ArchiveError) as err:
        await queued
    assert err.value.kind == ArchiveFailure.OWNER
    assert observed_threads == [caller_thread]
    assert not (await archive.async_page(SN)).rides


async def test_admission_full_rejects_without_growing_worker_queue(archive, monkeypatch):
    import custom_components.ninebot.ride_archive as module

    monkeypatch.setattr(module, "MAX_PENDING", 1)
    loop = asyncio.get_running_loop()
    begun = asyncio.Event()
    release = threading.Event()

    def blocked():
        loop.call_soon_threadsafe(begun.set)
        assert release.wait(5)

    task = asyncio.create_task(archive._run(blocked))
    await begun.wait()
    try:
        with pytest.raises(ArchiveError) as err:
            await archive.async_page(SN)
        assert err.value.kind == ArchiveFailure.BUSY and archive._pending == 1
    finally:
        release.set()
        await task


async def test_legacy_sql_full_is_atomic_and_retry_after_restart_does_not_need_source_rewrite(
    hass, tmp_path
):
    legacy = TravelStatisticsStore(hass, "entry")
    legacy.update(SN, sample(ids=tuple(f"legacy-{i}" for i in range(200))), NOW)
    original = json.dumps(legacy.dump(), sort_keys=True)
    path = tmp_path / "limited-import.sqlite3"
    limited = RideArchive(path, OWNER, max_bytes=128 * 1024)
    try:
        await limited.async_open()
        with pytest.raises(ArchiveError) as err:
            await limited.async_import_legacy(legacy.months, legacy.rides)
        assert err.value.kind == ArchiveFailure.CAPACITY
        assert await limited.async_month(SN, "202609") is None
        assert not (await limited.async_page(SN)).rides
    finally:
        await limited.async_close()
    recovered = RideArchive(path, OWNER)
    try:
        await recovered.async_open()
        assert await recovered.async_import_legacy(legacy.months, legacy.rides)
        assert len((await recovered.async_month(SN, "202609")).travel.rides) == 200
        assert json.dumps(legacy.dump(), sort_keys=True) == original
    finally:
        await recovered.async_close()


def test_retained_ambiguous_time_cannot_become_a_clean_calendar_fact_by_omission():
    before = replace(sample().rides[0], issues=("conflicting_time_representations",))
    sparse = parse_ride({"travel_id": "first", "ec": 121}, "202609")
    assert "conflicting_time_representations" in merged_ride(before, sparse).issues
    corrected = merged_ride(before, sample().rides[0])
    assert "conflicting_time_representations" not in corrected.issues
    changed_duration = merged_ride(corrected, replace(sample().rides[0], duration_s=700))
    assert "duration_time_difference" in changed_duration.issues


async def test_concurrent_open_uses_one_connection_and_duplicate_open_is_rejected(tmp_path):
    store = RideArchive(tmp_path / "concurrent.sqlite3", OWNER)
    try:
        results = await asyncio.gather(
            store.async_open(), store.async_open(), return_exceptions=True
        )
        assert results.count(None) == 1
        assert sum(isinstance(result, ArchiveError) for result in results) == 1
        await store.async_record_month(SN, sample(), NOW)
        assert len((await store.async_page(SN)).rides) == 2
    finally:
        await store.async_close()


async def test_corrupt_normalized_row_does_not_get_rewritten_during_open(archive, tmp_path):
    await archive.async_record_month(SN, sample(), NOW)
    path = tmp_path / "corrupt-row.sqlite3"
    await archive.async_backup(path)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT data FROM rides LIMIT 1").fetchone()
        data = json.loads(row[0])
        data["distance_m"] = "unreviewed"
        db.execute("UPDATE rides SET data=?", (json.dumps(data),))
    original = path.read_bytes()
    rejected = RideArchive(path, OWNER)
    try:
        with pytest.raises(ArchiveError) as err:
            await rejected.async_open()
        assert err.value.kind == ArchiveFailure.SCHEMA
        assert path.read_bytes() == original
    finally:
        await rejected.async_close()


async def test_month_summary_cannot_claim_more_identities_than_its_actual_list(archive):
    month = sample()
    with pytest.raises(ArchiveError) as err:
        await archive.async_record_month(SN, replace(month, rides=month.rides[:1]), NOW)
    assert err.value.kind == ArchiveFailure.SCHEMA
    assert await archive.async_month(SN, "202609") is None
    assert not (await archive.async_page(SN)).rides
