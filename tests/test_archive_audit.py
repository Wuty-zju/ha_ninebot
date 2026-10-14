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


async def test_completed_audit_status_exposes_later_changes_without_rewriting_its_evidence(archive):
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    completed = await finish(archive)
    before, revision, inventory = await archive.async_history_audit_status(SN)
    assert before == completed and revision == completed.cutoff_revision
    assert inventory == completed.cutoff_inventory
    await archive.async_record_month(
        "later-vehicle",
        (await archive.async_month(SN, "202610")).travel,
        NOW + timedelta(seconds=1),
    )
    after, revision, inventory = await archive.async_history_audit_status(SN)
    assert after == completed and revision > completed.cutoff_revision


async def test_raw_only_page_receipt_invalidates_completed_audit_without_projection_change(archive):
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    done = await finish(archive)
    _, before_revision, before_inventory = await archive.async_history_audit_status(SN)
    # A deliberately older receipt can add raw evidence without changing newer normalized rides.
    when = NOW - timedelta(seconds=2)
    run = await archive.async_schedule_history_run(SN, "202610", when, guard=lambda: True)
    raw = build_record(
        Endpoint.TRAVEL,
        {"times": 0, "list": [], "unknown_safe_field": "new raw evidence"},
        when,
        "202610",
        backend_version="0.1.7",
        endpoint_version=run.provider,
    )
    await archive.async_accept_history_page(SN, run, raw, guard=lambda: True)
    after, projection, inventory = await archive.async_history_audit_status(SN)
    assert after == done and projection == before_revision
    assert inventory > before_inventory == done.cutoff_inventory
    path = archive.path
    await archive.async_close()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        after, projection, persisted_inventory = await reopened.async_history_audit_status(SN)
        assert after == done and persisted_inventory == inventory
    finally:
        await reopened.async_close()


async def test_old_audit_without_inventory_fence_is_readable_but_not_certified(archive):
    import json

    from custom_components.ninebot.archive_audit import PREFIX, restored

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    with closing(sqlite3.connect(archive.path)) as db, db:
        key = PREFIX + vehicle_key(SN)
        body = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])
        del body["cutoff_inventory"]
        db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(body), key))
    legacy = await archive.async_history_audit(SN)
    assert legacy.complete and legacy.changed and not legacy.payloads_verified
    body["cutoff_inventory"] = True
    with pytest.raises(ValueError, match="inventory revision"):
        restored(json.dumps(body))


async def test_local_batch_reserves_decoded_budget_before_opening_next_object(archive):
    from unittest.mock import patch

    from custom_components.ninebot.const import MAX_RESPONSE_BYTES

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    with patch("custom_components.ninebot.archive_audit.MAX_BATCH_DECODED", MAX_RESPONSE_BYTES):
        first = await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=8)
    assert first.checked == 1 and first.phase == "detail"
    assert (await finish(archive)).payloads_verified


async def test_local_batch_yields_between_objects_after_time_budget(archive):
    from unittest.mock import patch

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    with patch("custom_components.ninebot.archive_audit.time") as clock:
        clock.monotonic.side_effect = [0, 1]
        first = await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=8)
    assert first.checked == 1 and first.phase == "detail"
    assert (await finish(archive)).payloads_verified


async def test_oversized_blob_is_flagged_from_metadata_without_opening_payload(archive):
    from unittest.mock import patch

    from custom_components.ninebot.archive_audit import MAX_COMPRESSED_OBJECT

    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute(
            "UPDATE detail_payloads SET body=? WHERE ride='ride-0'",
            (b"x" * (MAX_COMPRESSED_OBJECT + 1),),
        )
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    with patch("custom_components.ninebot.archive_audit.unpacked") as unpack:
        plan = await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    assert plan.checked == plan.invalid == 1
    unpack.assert_not_called()


async def test_background_audit_fairness_cooldown_restart_and_zero_cloud_budget(archive):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from custom_components.ninebot.preservation import HistoryPreservation

    other = "other-local-audit-vehicle"
    co = SimpleNamespace(
        config_entry=SimpleNamespace(options={"preserve_history": True, "auto_history_sync": True}),
        _stopping=False,
        _generation=1,
        _ownership={SN: 1, other: 1},
        data={SN: object(), other: object()},
        local_vehicle_available=lambda sn: sn in {SN, other},
        statistics=SimpleNamespace(
            archive=archive, writable=True, record_error=Mock(), _notify_change=Mock()
        ),
        async_query_detail=AsyncMock(),
        async_query_month=AsyncMock(),
        async_query_history_page=AsyncMock(),
    )
    co.preservation = HistoryPreservation(co)
    before = await archive.async_history_evidence(SN, guard=lambda: True)
    order = await archive.async_audit_order((SN, other))
    await co.preservation.audit.async_step(NOW)
    assert await archive.async_history_audit(order[0]) is not None
    assert await archive.async_history_audit(order[1]) is None
    # Reconstructing the worker does not reset the durable vehicle turn.
    co.preservation = HistoryPreservation(co)
    assert (await archive.async_audit_order((SN, other)))[0] == order[1]
    await co.preservation.audit.async_step(NOW)
    assert await archive.async_history_audit(order[1]) is not None
    for _ in range(4):
        await co.preservation.audit.async_step(NOW)
    done = await archive.async_history_audit(SN)
    assert done.complete and done.invalid == 0
    await co.preservation.audit.async_step(NOW + timedelta(hours=1))
    assert await archive.async_history_audit(SN) == done
    for _ in range(2):
        await co.preservation.audit.async_step(NOW + timedelta(days=1))
    assert (await archive.async_history_audit(SN)).started_at != done.started_at
    after = await archive.async_history_evidence(SN, guard=lambda: True)
    assert after.budget_at == before.budget_at
    co.async_query_detail.assert_not_awaited()
    co.async_query_month.assert_not_awaited()
    co.async_query_history_page.assert_not_awaited()


async def test_background_audit_respects_opt_out_pause_backup_and_local_ownership(archive):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from custom_components.ninebot.preservation import HistoryPreservation

    options = {"preserve_history": True, "auto_history_sync": False}
    co = SimpleNamespace(
        config_entry=SimpleNamespace(options=options),
        _stopping=False,
        _generation=1,
        _ownership={SN: 1},
        data={SN: object()},
        local_vehicle_available=lambda sn: sn == SN,
        statistics=SimpleNamespace(
            archive=archive, writable=True, record_error=Mock(), _notify_change=Mock()
        ),
    )
    co.preservation = HistoryPreservation(co)
    await co.preservation.audit.async_step(NOW)
    assert await archive.async_history_audit(SN) is None
    options["auto_history_sync"] = True
    await co.preservation.bootstrap.async_pause(SN, True)
    await co.preservation.audit.async_step(NOW)
    assert await archive.async_history_audit(SN) is None
    await co.preservation.bootstrap.async_pause(SN, False)
    await archive.async_prepare_backup()
    try:
        await co.preservation.audit.async_step(NOW)
        assert await archive.async_history_audit(SN) is None
    finally:
        archive.resume_after_backup()
    co._stopping = True
    await co.preservation.audit.async_step(NOW)
    assert await archive.async_history_audit(SN) is None
    co._stopping = False
    co.local_vehicle_available = lambda sn: False
    await co.preservation.audit.async_step(NOW)
    assert await archive.async_history_audit(SN) is None
