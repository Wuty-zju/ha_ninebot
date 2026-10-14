"""Bounded, resumable real-SQLite verification of synthetic local evidence."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.archive_audit import FAULT_PREFIX
from custom_components.ninebot.history_plan import HistoryPlan
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import (
    ArchiveError,
    ArchiveFailure,
    RideArchive,
    vehicle_key,
)
from custom_components.ninebot.travel import merge_detail, parse_ride

NOW = datetime(2026, 10, 14, tzinfo=UTC)
SN = "synthetic-audit-vehicle"
OWNER = hashlib.sha256(b"synthetic-audit-owner").hexdigest()


@pytest.fixture
async def archive(tmp_path):
    store = RideArchive(tmp_path / "audit.sqlite3", OWNER)
    await store.async_open()
    root = HistoryPlan.create(
        "a" * 32, vehicle_key(SN), "202610", "202610", (NOW - timedelta(seconds=1)).isoformat()
    )
    await store.async_set_history_plan(SN, root, None, guard=lambda: True)
    rows = [
        {
            "travel_id": f"ride-{i}",
            "mileages": 1,
            "duration": 60,
            "ec": 10,
            "start_time": int(NOW.timestamp()) - 3600 + i * 120,
            "end_time": int(NOW.timestamp()) - 3540 + i * 120,
        }
        for i in range(3)
    ]
    raw = build_record(
        Endpoint.TRAVEL,
        {"times": 3, "list": rows},
        NOW - timedelta(seconds=1),
        "202610",
        backend_version="0.1.7",
        endpoint_version=root.provider,
    )
    await store.async_accept_history_page(SN, root, raw, guard=lambda: True)
    month = await store.async_month(SN, "202610")
    for row in rows:
        body = {**row, "trail": "120,30,1,1;120.01,30.01,2,2"}
        raw = build_record(Endpoint.TRIP_DETAIL, body, NOW, "202610", backend_version="0.1.7")
        matched = next(item for item in month.travel.rides if item.ride_id == row["travel_id"])
        assert await store.async_record_detail(
            SN,
            merge_detail(matched, parse_ride(body, "202610", source=Endpoint.TRIP_DETAIL)),
            NOW,
            raw=raw,
        )
    try:
        yield store
    finally:
        await store.async_close()


async def finish(store):
    for _ in range(10):
        plan = await store.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
        if plan.complete:
            return plan
    pytest.fail("Bounded audit did not terminate")


async def test_batches_restart_and_coalescing_preserve_checkpoint(archive):
    plan = await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    assert plan.total == 4 and not plan.complete
    first = await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    assert first.checked == 1
    assert (
        await archive.async_start_history_audit(SN, NOW, guard=lambda: True, restart=True) == first
    )
    path = archive.path
    await archive.async_close()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        assert await reopened.async_history_audit(SN) == first
        done = await finish(reopened)
        assert done.checked == 4 and done.invalid == 0 and done.payloads_verified
        assert await reopened.async_step_history_audit(SN, NOW, guard=lambda: True) == done
    finally:
        await reopened.async_close()


async def test_corruption_is_indexed_and_original_bytes_remain_while_good_objects_continue(archive):
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    damaged = b"keep-this-corrupted-original"
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE detail_payloads SET body=? WHERE ride='ride-1'", (damaged,))
    done = await finish(archive)
    assert done.checked == 4 and done.invalid == 1 and not done.payloads_verified
    assert await archive.async_private_detail(SN, "ride-0") is not None
    with closing(sqlite3.connect(archive.path)) as db:
        faults = db.execute(
            "SELECT value FROM meta WHERE key LIKE ?", (FAULT_PREFIX + "%",)
        ).fetchall()
        assert faults == [("payload",)]
        assert (
            db.execute("SELECT body FROM detail_payloads WHERE ride='ride-1'").fetchone()[0]
            == damaged
        )


async def test_concurrent_archive_changes_do_not_claim_current_whole_archive_verified(archive):
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    await archive.async_record_month(
        "other-vehicle",
        (await archive.async_month(SN, "202610")).travel,
        NOW + timedelta(seconds=1),
    )
    done = await finish(archive)
    assert done.complete and done.invalid == 0 and done.changed and not done.payloads_verified


async def test_guard_and_backup_barriers_do_not_advance_audit(archive):
    before = await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    with pytest.raises(ArchiveError) as denied:
        await archive.async_step_history_audit(SN, NOW, guard=lambda: False)
    assert denied.value.kind is ArchiveFailure.OWNER
    assert await archive.async_history_audit(SN) == before
    await archive.async_prepare_backup()
    try:
        with pytest.raises(ArchiveError) as paused:
            await archive.async_step_history_audit(SN, NOW, guard=lambda: True)
        assert paused.value.kind is ArchiveFailure.BUSY
    finally:
        archive.resume_after_backup()
    assert await archive.async_history_audit(SN) == before
