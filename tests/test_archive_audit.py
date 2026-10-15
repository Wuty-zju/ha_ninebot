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
    with patch("custom_components.ninebot.object_checks.unpacked") as unpack:
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


async def test_scoped_receipts_reuse_old_bodies_and_only_decode_new_unknown_fields(archive):
    from unittest.mock import patch

    from custom_components.ninebot.object_checks import ObjectSummary, unpacked

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    checks = await archive.async_object_checks(SN)
    assert checks.receipts_complete and checks.verified == checks.total == 4
    with patch("custom_components.ninebot.object_checks.unpacked", wraps=unpacked) as decode:
        result = await archive.async_background_history_audit(SN, NOW, guard=lambda: True)
        assert isinstance(result, ObjectSummary) and result.pending == 0
        decode.assert_not_called()
        saved = await archive.async_private_detail(SN, "ride-0")
        body = {**saved.latest.payload(), "unknown_safe_extra": "new evidence"}
        raw = build_record(
            Endpoint.TRIP_DETAIL,
            body,
            NOW + timedelta(seconds=1),
            "202610",
            backend_version="0.1.7",
        )
        assert await archive.async_record_detail(
            SN,
            merge_detail(saved.ride, parse_ride(body, "202610", source=Endpoint.TRIP_DETAIL)),
            raw.received_at,
            raw=raw,
        )
        assert (await archive.async_object_checks(SN)).pending == 1
        result = await archive.async_background_history_audit(
            SN,
            NOW + timedelta(seconds=2),
            guard=lambda: True,
        )
        assert isinstance(result, ObjectSummary) and result.receipts_complete
        assert decode.call_count == 1
        assert (
            await archive.async_background_history_audit(
                SN,
                NOW + timedelta(seconds=3),
                guard=lambda: True,
            )
            is None
        )
        assert decode.call_count == 1


async def test_receipt_contract_change_invalidates_seen_fence_without_raw_changes(archive):
    from unittest.mock import patch

    from custom_components.ninebot.object_checks import ObjectSummary, unpacked

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    await archive.async_background_history_audit(SN, NOW, guard=lambda: True)
    with (
        patch("custom_components.ninebot.object_checks.CONTRACT", 2),
        patch("custom_components.ninebot.object_checks.unpacked", wraps=unpacked) as decode,
    ):
        first = await archive.async_background_history_audit(SN, NOW, guard=lambda: True)
        assert isinstance(first, ObjectSummary) and first.pending == 2
        second = await archive.async_background_history_audit(SN, NOW, guard=lambda: True)
        assert isinstance(second, ObjectSummary) and second.receipts_complete
        assert decode.call_count == 4
    assert (await archive.async_object_checks(SN)).pending == 4


async def test_receipts_bind_metadata_and_cannot_be_reused_by_a_replaced_rowid(archive):
    from unittest.mock import patch

    from custom_components.ninebot.object_checks import ObjectSummary, unpacked

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    # A metadata change is a new check scope even when body/hash/rowid are the same.
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE detail_payloads SET endpoint='0.1.7' WHERE ride='ride-0'")
    assert (await archive.async_object_checks(SN)).pending == 1
    with patch("custom_components.ninebot.object_checks.unpacked", wraps=unpacked) as decode:
        result = await archive.async_background_history_audit(SN, NOW, guard=lambda: True)
        assert isinstance(result, ObjectSummary) and result.receipts_complete
        assert decode.call_count == 1
    with closing(sqlite3.connect(archive.path)) as db, db:
        row = db.execute("SELECT rowid,hash FROM detail_payloads WHERE ride='ride-1'").fetchone()
        # Deliberate synthetic out-of-band row replacement; the old receipt must not match.
        db.execute("UPDATE detail_payloads SET hash=? WHERE rowid=?", ("f" * 64, row[0]))
    checks = await archive.async_object_checks(SN)
    assert checks.pending == 1 and not checks.receipts_complete


async def test_quarantine_deduplicates_real_bad_bytes_and_survives_consistent_backup(
    archive, tmp_path
):
    damaged = b"preserve-this-exact-corrupt-object"
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE detail_payloads SET body=? WHERE ride='ride-1'", (damaged,))
    for _ in range(2):
        await archive.async_start_history_audit(SN, NOW, guard=lambda: True, restart=True)
        done = await finish(archive)
        assert done.invalid == 1
    checks = await archive.async_object_checks(SN)
    assert checks.invalid == checks.quarantined == 1 and not checks.receipts_complete
    with closing(sqlite3.connect(archive.path)) as db:
        rows = db.execute(
            "SELECT storage_mode,body,body_hash,source_size FROM payload_quarantine"
        ).fetchall()
        assert rows == [("copied", damaged, hashlib.sha256(damaged).hexdigest(), len(damaged))]
        assert (
            db.execute("SELECT body FROM detail_payloads WHERE ride='ride-1'").fetchone()[0]
            == damaged
        )
    path = tmp_path / "quarantine-backup.sqlite3"
    await archive.async_backup(path)
    restored = RideArchive(path, OWNER)
    try:
        await restored.async_open()
        assert (await restored.async_object_checks(SN)).quarantined == 1
        assert await restored.async_private_detail(SN, "ride-0") is not None
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT body FROM payload_quarantine").fetchone()[0] == damaged
    finally:
        await restored.async_close()


async def test_oversized_source_is_pinned_in_place_and_missing_page_is_not_claimed_copied(archive):
    from custom_components.ninebot.archive_audit import MAX_COMPRESSED_OBJECT

    size = MAX_COMPRESSED_OBJECT + 1
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE detail_payloads SET body=? WHERE ride='ride-0'", (b"x" * size,))
        db.execute("DELETE FROM history_payloads")
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    done = await finish(archive)
    assert done.invalid == 2
    with closing(sqlite3.connect(archive.path)) as db:
        rows = db.execute(
            "SELECT kind,storage_mode,body,body_hash,source_size FROM payload_quarantine"
            " ORDER BY kind"
        ).fetchall()
        assert rows == [
            ("detail", "in_place", None, None, size),
            ("page", "missing", None, None, 0),
        ]
        assert (
            db.execute("SELECT length(body) FROM detail_payloads WHERE ride='ride-0'").fetchone()[0]
            == size
        )


async def test_schema4_migration_preserves_payloads_cursor_and_old_schema_backup(archive, tmp_path):
    import shutil

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    checkpoint = await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    path = archive.path
    await archive.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE payload_checks")
        db.execute("DROP TABLE payload_quarantine")
        db.execute("PRAGMA user_version=4")
        originals = db.execute(
            "SELECT vehicle,ride,hash,body FROM detail_payloads ORDER BY ride"
        ).fetchall()
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    migrated = RideArchive(path, OWNER)
    try:
        await migrated.async_open()
        after = await migrated.async_history_audit(SN)
        assert after.checked == checkpoint.checked and after.after == checkpoint.after
        assert after.changed and not after.payloads_verified
        assert (await migrated.async_object_checks(SN)).pending == 4
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 5
            assert (
                db.execute(
                    "SELECT vehicle,ride,hash,body FROM detail_payloads ORDER BY ride"
                ).fetchall()
                == originals
            )
        copies = list(path.parent.glob(f"rides.schema4-{original_hash}.sqlite3"))
        assert len(copies) == 1
        with closing(sqlite3.connect(copies[0])) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4
            assert (
                db.execute(
                    "SELECT vehicle,ride,hash,body FROM detail_payloads ORDER BY ride"
                ).fetchall()
                == originals
            )
        # Restore by copying, never migrating the only recovery original in place.
        restored_path = tmp_path / "restored" / "rides.sqlite3"
        restored_path.parent.mkdir()
        shutil.copyfile(copies[0], restored_path)
        restored = RideArchive(restored_path, OWNER)
        try:
            await restored.async_open()
            assert (await restored.async_private_detail(SN, "ride-0")).latest is not None
        finally:
            await restored.async_close()
    finally:
        await migrated.async_close()


async def test_schema5_failed_ddl_rolls_back_original_with_recovery_copy(archive, monkeypatch):
    from custom_components.ninebot import object_checks

    path = archive.path
    await archive.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE payload_checks")
        db.execute("DROP TABLE payload_quarantine")
        db.execute("PRAGMA user_version=4")
        bodies = db.execute("SELECT body FROM detail_payloads ORDER BY ride").fetchall()
    monkeypatch.setattr(object_checks, "SCHEMA", (object_checks.SCHEMA[0], "INVALID MIGRATION DDL"))
    migrated = RideArchive(path, OWNER)
    try:
        with pytest.raises(ArchiveError) as error:
            await migrated.async_open()
        assert error.value.kind is ArchiveFailure.STORAGE
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4
            assert db.execute("SELECT body FROM detail_payloads ORDER BY ride").fetchall() == bodies
            assert not db.execute(
                "SELECT name FROM sqlite_master WHERE name='payload_checks'"
            ).fetchall()
        assert len(list(path.parent.glob("rides.schema4-*.sqlite3"))) == 1
    finally:
        await migrated.async_close()


async def test_pinned_oversized_blob_does_not_block_healthy_reads_after_restart(archive):
    from custom_components.ninebot.archive_audit import MAX_COMPRESSED_OBJECT

    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute(
            "UPDATE detail_payloads SET body=? WHERE ride='ride-0'",
            (b"x" * (MAX_COMPRESSED_OBJECT + 1),),
        )
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    path = archive.path
    await archive.async_close()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        assert await reopened.async_private_detail(SN, "ride-1") is not None
        with pytest.raises(ArchiveError) as error:
            await reopened.async_private_detail(SN, "ride-0")
        assert error.value.kind is ArchiveFailure.OBJECT
        assert (await reopened.async_object_checks(SN)).quarantined == 1
        with closing(sqlite3.connect(path)) as db:
            assert (
                db.execute(
                    "SELECT length(body) FROM detail_payloads WHERE ride='ride-0'"
                ).fetchone()[0]
                == MAX_COMPRESSED_OBJECT + 1
            )
    finally:
        await reopened.async_close()


async def test_per_vehicle_delta_fence_avoids_rechecking_unrelated_car(archive):
    from unittest.mock import patch

    from custom_components.ninebot.payload_inventory import advance

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    await archive.async_background_history_audit(SN, NOW, guard=lambda: True)
    # A different car's managed raw mutation must not schedule an identical delta scan.
    with closing(sqlite3.connect(archive.path)) as db, db:
        advance(db, vehicle_key("other-car"))
    with patch("custom_components.ninebot.archive_audit.delta_step") as delta:
        assert await archive.async_background_history_audit(SN, NOW, guard=lambda: True) is None
        delta.assert_not_called()


async def page_check_status(store):
    return (await store.async_history_verification_status(SN))[3].page_references


@pytest.mark.parametrize(
    "fault",
    [
        "header_ids",
        "header_total",
        "missing_index",
        "first_page",
        "extra_index",
        "missing_membership",
        "missing_ride",
        "invalid_header",
    ],
)
async def test_good_raw_bytes_do_not_certify_conflicting_page_links(archive, fault):
    with closing(sqlite3.connect(archive.path)) as db, db:
        originals = db.execute("SELECT digest,body FROM history_payloads").fetchall()
        if fault == "header_ids":
            db.execute("UPDATE history_pages SET ids=?", ('{"ids":["wrong"]}',))
        elif fault == "header_total":
            db.execute("UPDATE history_pages SET total=4")
        elif fault == "missing_index":
            db.execute("DELETE FROM history_ids WHERE ride='ride-0'")
        elif fault == "first_page":
            db.execute("UPDATE history_ids SET first_page=2 WHERE ride='ride-0'")
        elif fault == "extra_index":
            row = db.execute("SELECT vehicle,plan,month FROM history_ids LIMIT 1").fetchone()
            db.execute("INSERT INTO history_ids VALUES (?,?,?,?,1)", (*row, "unexpected"))
        elif fault == "missing_membership":
            db.execute("DELETE FROM membership WHERE ride='ride-0'")
        elif fault == "missing_ride":
            db.execute("DELETE FROM rides WHERE id='ride-0'")
        else:
            db.execute("UPDATE history_pages SET ids='{' ")
        damaged_headers = db.execute("SELECT ids,total FROM history_pages").fetchall()
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    raw = await finish(archive)
    assert raw.payloads_verified  # It remains a raw-bytes contract, not index proof.
    checks = await page_check_status(archive)
    assert checks.complete and checks.total == checks.checked == checks.conflicts == 1
    assert not checks.unverifiable and not checks.captured_links_verified
    assert (await archive.async_object_checks(SN)).receipts_complete
    with closing(sqlite3.connect(archive.path)) as db:
        assert db.execute("SELECT digest,body FROM history_payloads").fetchall() == originals
        assert db.execute("SELECT ids,total FROM history_pages").fetchall() == damaged_headers
        assert not db.execute("SELECT * FROM payload_quarantine").fetchall()


async def test_page_reference_checkpoint_survives_restart_without_second_raw_decode(archive):
    from unittest.mock import patch

    from custom_components.ninebot import object_checks

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    before = await page_check_status(archive)
    assert before.total == 1 and before.checked == 0 and not before.complete
    active = await archive.async_history_audit(SN)
    assert (
        await archive.async_start_history_audit(SN, NOW, guard=lambda: True, restart=True) == active
    )
    assert await page_check_status(archive) == before  # Active start coalesces.
    path = archive.path
    await archive.async_close()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        assert await page_check_status(reopened) == before
        with patch(
            "custom_components.ninebot.object_checks.unpacked", wraps=object_checks.unpacked
        ) as decode:
            assert (await finish(reopened)).payloads_verified
        # Two remaining details plus one page; page references reused the one decode.
        assert decode.call_count == 3
        checks = await page_check_status(reopened)
        assert checks.complete and checks.captured_links_verified
        assert checks.total == checks.checked == 1
        assert not checks.conflicts and not checks.unverifiable
    finally:
        await reopened.async_close()


async def test_legacy_raw_scan_does_not_gain_a_partial_page_reference_certificate(archive):
    from custom_components.ninebot.page_reference_audit import key

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)

    def remove_new_checkpoint():
        db = archive._db(write=True)
        with db:
            db.execute("DELETE FROM meta WHERE key=?", (key(vehicle_key(SN)),))

    await archive._run(remove_new_checkpoint)
    assert (await finish(archive)).payloads_verified
    assert await page_check_status(archive) is None
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True, restart=True)
    await finish(archive)
    assert (await page_check_status(archive)).captured_links_verified


async def test_page_reference_failure_rolls_back_raw_receipt_and_both_checkpoints(
    archive, monkeypatch
):
    from custom_components.ninebot import page_reference_audit

    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    for _ in range(3):
        await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    before = await archive.async_history_audit(SN)
    references = await page_check_status(archive)

    def fail(*args):
        raise ValueError("Synthetic page checkpoint failure")

    monkeypatch.setattr(page_reference_audit, "put", fail)
    with pytest.raises(ArchiveError):
        await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    assert await archive.async_history_audit(SN) == before
    assert await page_check_status(archive) == references
    receipts = await archive.async_object_checks(SN)
    assert receipts.verified == 3 and receipts.pending == 1


@pytest.mark.parametrize("fault", ["unsupported", "damaged_payload"])
async def test_unverifiable_page_is_not_mislabeled_as_reference_conflict(archive, fault):
    with closing(sqlite3.connect(archive.path)) as db, db:
        if fault == "unsupported":
            db.execute("UPDATE history_pages SET endpoint='0.2.0+unknown'")
        else:
            db.execute("UPDATE history_payloads SET body=?", (b"broken",))
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await finish(archive)
    checks = await page_check_status(archive)
    assert checks.unverifiable == 1 and checks.conflicts == 0
    assert not checks.captured_links_verified


async def test_reference_summary_does_not_certify_changed_archive_scope(archive):
    await archive.async_start_history_audit(SN, NOW, guard=lambda: True)
    await archive.async_step_history_audit(SN, NOW, guard=lambda: True, limit=1)
    await archive.async_record_month(
        "other-car", (await archive.async_month(SN, "202610")).travel, NOW + timedelta(seconds=1)
    )
    await finish(archive)
    checks = await page_check_status(archive)
    assert checks.complete and not checks.conflicts and not checks.unverifiable
    assert not checks.scope_unchanged and not checks.captured_links_verified
