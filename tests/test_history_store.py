"""Real account-local SQL transactions with synthetic private page evidence."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot import history_store
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.history_plan import HistoryPlan, HistoryState
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import (
    ArchiveError,
    ArchiveFailure,
    RideArchive,
    vehicle_key,
)

NOW = datetime(2026, 10, 14, tzinfo=UTC)
OWNER = hashlib.sha256(b"synthetic-history-account").hexdigest()
SN = "synthetic-history-vehicle"


def initial():
    return HistoryPlan.create("a" * 32, vehicle_key(SN), "202609", "202610", NOW.isoformat())


def record(ids=("ride-one",), total=2, month="202610", **extra):
    return build_record(
        Endpoint.TRAVEL,
        {"times": total, "list": [{"travel_id": i} for i in ids], **extra},
        NOW,
        month,
        backend_version="0.1.7",
        endpoint_version="0.1.7+native.history.v1",
    )


@pytest.fixture
async def store(tmp_path):
    archive = RideArchive(tmp_path / "history.sqlite3", OWNER)
    await archive.async_open()
    await archive.async_set_history_plan(SN, initial(), None, guard=lambda: True)
    try:
        yield archive
    finally:
        await archive.async_close()


async def test_pages_checkpoint_reopen_backup_and_private_redaction(store, tmp_path):
    first = await store.async_accept_history_page(
        SN, initial(), record(password="never-retain", unknown={"future": 3}), guard=lambda: True
    )
    assert not first.finished and first.root.next_page == 2
    saved = await store.async_history_page(SN, initial().plan_id, "202610", 1, guard=lambda: True)
    assert saved.payload()["password"] == "[redacted]"
    assert saved.payload()["unknown"] == {"future": 3}
    second = await store.async_accept_history_page(
        SN, first.root, record(("ride-two",)), guard=lambda: True
    )
    assert second.verified and second.unique_count == 2
    assert second.root.next_month == "202609"
    empty = await store.async_accept_history_page(
        SN, second.root, record((), 0, "202609"), guard=lambda: True
    )
    assert empty.root.state is HistoryState.PRESERVE
    assert empty.root.discovery_complete and empty.root.verified_months == 2
    assert empty.root.state is not HistoryState.COMPLETE
    backup = tmp_path / "backup.sqlite3"
    await store.async_backup(backup)
    await store.async_close()
    restored = RideArchive(backup, OWNER)
    try:
        await restored.async_open()
        assert await restored.async_history_plan(SN) == empty.root
        assert (
            await restored.async_history_page(
                SN, initial().plan_id, "202610", 1, guard=lambda: True
            )
        ).payload() == saved.payload()
    finally:
        await restored.async_close()


async def test_checkpoint_failure_rolls_back_raw_page_and_manifest(store, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("synthetic rollback")

    monkeypatch.setattr(history_store, "put_root", fail)
    with pytest.raises(ArchiveError):
        await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert await store.async_history_plan(SN) == initial()
    assert (
        await store.async_history_page(SN, initial().plan_id, "202610", 1, guard=lambda: True)
        is None
    )


async def test_stale_claim_scope_and_revocation_do_not_commit(store):
    first = await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    with pytest.raises(ArchiveError) as error:
        await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert error.value.kind is ArchiveFailure.BUSY
    with pytest.raises(ArchiveError) as error:
        await store.async_accept_history_page(
            SN, first.root, record(("ride-two",)), guard=lambda: False
        )
    assert error.value.kind is ArchiveFailure.OWNER
    with pytest.raises(ValueError):
        await store.async_accept_history_page("foreign", first.root, record(), guard=lambda: True)
    assert await store.async_history_plan(SN) == first.root


@pytest.mark.parametrize(
    "ids,total,reason",
    [
        (("ride-one",), 2, "repeated_page"),
        (("ride-two",), 3, "unstable_total"),
        ((), 2, "empty_page"),
        (("ride-two",), None, "missing_total"),
        (("ride-two", "ride-two"), 2, "schema"),
    ],
)
async def test_incomplete_pages_leave_explicit_gap_and_continue_other_months(
    store, ids, total, reason
):
    first = await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    bad = await store.async_accept_history_page(
        SN, first.root, record(ids, total), guard=lambda: True
    )
    assert bad.stop == reason and bad.finished and not bad.verified
    assert bad.root.gap_months == 1 and bad.root.next_month == "202609"
    assert bad.root.state is HistoryState.DISCOVER


async def test_schema2_migration_preserves_original_and_creates_no_fake_baseline(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    await archive.async_record_month(
        SN,
        travel(
            {
                "times": 1,
                "list": [
                    {
                        "travel_id": "legacy",
                        "mileages": 1,
                        "duration": 60,
                        "start_time": int(NOW.timestamp()),
                        "end_time": int(NOW.timestamp()) + 60,
                    }
                ],
            },
            "202610",
        ),
        NOW,
    )
    await archive.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE payload_checks")
        db.execute("DROP TABLE payload_quarantine")
        db.execute("DROP TABLE history_payloads")
        db.execute("DROP TABLE history_ids")
        db.execute("DROP TABLE history_months")
        db.execute("DROP TABLE history_pages")
        db.execute("DROP TABLE history_roots")
        db.execute("PRAGMA user_version=2")
    before = path.read_bytes()
    migrated = RideArchive(path, OWNER)
    try:
        await migrated.async_open()
        assert await migrated.async_history_plan(SN) is None
        copies = list(tmp_path.glob("rides.schema2-*.sqlite3"))
        assert len(copies) == 1 and hashlib.sha256(before).hexdigest() in copies[0].name
        with closing(sqlite3.connect(copies[0])) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
            assert backup.execute("SELECT count(*) FROM rides").fetchone()[0] == 1
        assert (await migrated.async_month(SN, "202610")).travel.rides[0].ride_id == "legacy"
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        await migrated.async_set_history_plan(SN, initial(), None, guard=lambda: True)
    finally:
        await migrated.async_close()


async def test_capacity_and_backup_barrier_leave_checkpoint_readable(store):
    store.write_paused = True
    with pytest.raises(ArchiveError) as error:
        await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert error.value.kind is ArchiveFailure.CAPACITY
    store.write_paused = False
    await store.async_prepare_backup()
    with pytest.raises(ArchiveError) as error:
        await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert error.value.kind is ArchiveFailure.BUSY
    assert await store.async_history_plan(SN) == initial()
    store.resume_after_backup()


async def test_quota_reduction_preserves_data_and_can_be_reopened(store):
    first = await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert not await store.async_set_capacity(32768, guard=lambda: True)
    assert await store.async_history_plan(SN) == first.root
    assert await store.async_set_capacity(200 * 1024 * 1024, guard=lambda: True)
    second = await store.async_accept_history_page(
        SN, first.root, record(("ride-two",)), guard=lambda: True
    )
    assert second.verified


async def test_free_space_migration_preflight_does_not_touch_original(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from custom_components.ninebot import ride_archive

    path = tmp_path / "no-space.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    await archive.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE payload_checks")
        db.execute("DROP TABLE payload_quarantine")
        db.execute("DROP TABLE history_payloads")
        db.execute("DROP TABLE history_ids")
        db.execute("DROP TABLE history_months")
        db.execute("DROP TABLE history_pages")
        db.execute("DROP TABLE history_roots")
        db.execute("PRAGMA user_version=2")
    before = path.read_bytes()
    monkeypatch.setattr(ride_archive.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    migrated = RideArchive(path, OWNER)
    try:
        with pytest.raises(ArchiveError) as error:
            await migrated.async_open()
        assert error.value.kind is ArchiveFailure.CAPACITY
        assert path.read_bytes() == before
        assert not list(tmp_path.glob("rides.schema2-*.sqlite3"))
    finally:
        await migrated.async_close()


async def test_claim_lease_and_shared_budget_survive_restart(store):
    claimed = await store.async_claim_history_page(SN, NOW, 60, guard=lambda: True)
    assert claimed.state is HistoryState.QUERYING and claimed.attempts == 1
    assert (
        await store.async_claim_history_page(
            SN, NOW + timedelta(seconds=61), 60, guard=lambda: True
        )
        is None
    )
    path = store.path
    await store.async_close()
    resumed = RideArchive(path, OWNER)
    try:
        await resumed.async_open()
        assert await resumed.async_history_plan(SN) == claimed
        assert not await resumed.async_background_budget(
            SN, NOW + timedelta(seconds=10), 60, guard=lambda: True
        )
        assert (
            await resumed.async_claim_history_page(
                SN, NOW + timedelta(seconds=119), 60, guard=lambda: True
            )
            is None
        )
        next_claim = await resumed.async_claim_history_page(
            SN, NOW + timedelta(seconds=121), 60, guard=lambda: True
        )
        assert next_claim.next_page == 1 and next_claim.attempts == 2
        await resumed.async_wait_history_page(
            next_claim, NOW + timedelta(seconds=122), "connection", 300, guard=lambda: True
        )
        waiting = await resumed.async_history_plan(SN)
        assert waiting.state is HistoryState.WAITING
        assert (
            await resumed.async_claim_history_page(
                SN, NOW + timedelta(seconds=300), 60, guard=lambda: True
            )
            is None
        )
    finally:
        await resumed.async_close()


async def test_no_cursor_can_advance_without_its_page_evidence(store):
    forged = initial().page_committed(NOW.isoformat(), finished=True, verified=True)
    with pytest.raises(ArchiveError) as error:
        await store.async_set_history_plan(SN, forged, initial(), guard=lambda: True)
    assert error.value.kind is ArchiveFailure.SCHEMA
    assert await store.async_history_plan(SN) == initial()


async def test_page_rides_and_legacy_job_share_no_checkpoint(store):
    from custom_components.ninebot.sync_job import SyncJob, SyncState

    old = SyncJob(
        "c" * 32,
        vehicle_key(SN),
        "202609",
        "202610",
        "202610",
        NOW.isoformat(),
        NOW.isoformat(),
        state=SyncState.QUERYING,
    )
    await store.async_set_sync_job(old, None, guard=lambda: True)
    first = await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    await store.async_accept_history_page(SN, first.root, record(("ride-two",)), guard=lambda: True)
    assert await store.async_sync_job() == old
    page = await store.async_page(SN)
    assert {row.ride_id for row in page.rides} == {"ride-one", "ride-two"}
    assert (
        await store.async_detail_summary(SN, "ride-one", "202610", guard=lambda: True)
    ).ride_id == "ride-one"


async def test_normalization_failure_rolls_back_page_cursor_and_rides(store, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic normalization rollback")

    monkeypatch.setattr(store, "_record_month", fail)
    with pytest.raises(ArchiveError):
        await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert await store.async_history_plan(SN) == initial()
    assert not (await store.async_page(SN)).rides
    assert (
        await store.async_history_page(SN, initial().plan_id, "202610", 1, guard=lambda: True)
        is None
    )


async def completed_discovery(store):
    first = await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    second = await store.async_accept_history_page(
        SN, first.root, record(("ride-two",)), guard=lambda: True
    )
    last = await store.async_accept_history_page(
        SN, second.root, record((), 0, "202609"), guard=lambda: True
    )
    return last.root


async def save_track(store, identity, trail, *, simplified=False):
    from custom_components.ninebot.travel import parse_ride

    body = {"travel_id": identity, "trail": trail, "is_show_simple_point": simplified}
    raw = build_record(
        Endpoint.TRIP_DETAIL, body, NOW + timedelta(seconds=3), "202610", backend_version="0.1.7"
    )
    ride = parse_ride(body, "202610", source=Endpoint.TRIP_DETAIL)
    await store.async_record_detail(
        SN, ride, raw.received_at, guard=lambda: True, ordered=True, raw=raw
    )


async def test_baseline_waits_for_details_and_reports_simplified_geometry(store):
    from dataclasses import replace

    root = await completed_discovery(store)
    health = await store.async_history_health(SN, root.plan_id)
    assert health.discovered == health.missing == 2 and health.saved == 0
    assert (
        await store.async_finish_history_baseline(
            SN, root, NOW + timedelta(seconds=10), guard=lambda: True
        )
        == root
    )
    with pytest.raises(ArchiveError):
        await store.async_set_history_plan(
            SN,
            replace(root, state=HistoryState.COMPLETE, revision=root.revision + 1),
            root,
            guard=lambda: True,
        )
    await save_track(store, "ride-one", "120,30,1,1;120.1,30.1,2,2")
    assert (await store.async_history_health(SN, root.plan_id)).missing == 1
    await save_track(store, "ride-two", "120,30,0,0", simplified=True)
    health = await store.async_history_health(SN, root.plan_id)
    assert health.saved == 2 and health.quality_pending == 0 and health.simplified == 1
    finished = await store.async_finish_history_baseline(
        SN, root, NOW + timedelta(seconds=10), guard=lambda: True
    )
    assert finished.state is HistoryState.WITH_GAPS


async def test_best_geometry_retention_and_local_quality_backfill_need_no_cloud(store):
    root = await completed_discovery(store)
    for identity in ("ride-one", "ride-two"):
        await save_track(store, identity, "120,30,1,1;120.1,30.1,2,2")
    # A newer simplified response must preserve the previously saved geometry.
    await save_track(store, "ride-one", "120,30,0,0", simplified=True)
    health = await store.async_history_health(SN, root.plan_id)
    assert health.latest_degraded == 1 and health.simplified == 0
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("DELETE FROM meta WHERE key LIKE 'detail_health:%'")
    assert (await store.async_history_health(SN, root.plan_id)).quality_pending == 2
    assert (
        await store.async_backfill_detail_health(SN, root.plan_id, guard=lambda: True, limit=1) == 1
    )
    assert (await store.async_history_health(SN, root.plan_id)).quality_pending == 1
    assert (
        await store.async_backfill_detail_health(SN, root.plan_id, guard=lambda: True, limit=1) == 1
    )
    assert (
        await store.async_backfill_detail_health(SN, root.plan_id, guard=lambda: True, limit=1) == 0
    )
    finished = await store.async_finish_history_baseline(
        SN, root, NOW + timedelta(seconds=10), guard=lambda: True
    )
    assert finished.state is HistoryState.COMPLETE
    await store.async_close()
    recovered = RideArchive(store.path, OWNER)
    try:
        await recovered.async_open()
        assert await recovered.async_history_plan(SN) == finished
        assert (await recovered.async_history_health(SN, root.plan_id)).latest_degraded == 1
    finally:
        await recovered.async_close()


async def test_pause_is_durable_does_not_reset_root_or_shared_budget(store):
    claimed = await store.async_claim_history_page(SN, NOW, 60, guard=lambda: True)
    await store.async_set_history_paused(SN, True, guard=lambda: True)
    assert await store.async_history_paused(SN)
    assert (
        await store.async_claim_history_page(SN, NOW + timedelta(minutes=3), 60, guard=lambda: True)
        is None
    )
    assert not await store.async_background_budget(
        SN, NOW + timedelta(minutes=3), 60, guard=lambda: True
    )
    assert await store.async_history_plan(SN) == claimed
    path = store.path
    await store.async_close()
    recovered = RideArchive(path, OWNER)
    try:
        await recovered.async_open()
        assert await recovered.async_history_paused(SN)
        assert await recovered.async_history_plan(SN) == claimed
        await recovered.async_set_history_paused(SN, False, guard=lambda: True)
        resumed = await recovered.async_claim_history_page(
            SN, NOW + timedelta(minutes=3), 60, guard=lambda: True
        )
        assert resumed.plan_id == claimed.plan_id and resumed.next_page == claimed.next_page
        assert resumed.attempts == claimed.attempts + 1
    finally:
        await recovered.async_close()


async def test_paused_actor_rejects_inflight_page_even_if_callers_guard_is_stale(store):
    await store.async_set_history_paused(SN, True, guard=lambda: True)
    with pytest.raises(ArchiveError) as error:
        await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    assert error.value.kind is ArchiveFailure.BUSY
    assert await store.async_history_plan(SN) == initial()
    assert (
        await store.async_history_page(SN, initial().plan_id, "202610", 1, guard=lambda: True)
        is None
    )


async def test_incremental_month_run_coalesces_and_never_replaces_initial_baseline(store):
    baseline = await store.async_history_plan(SN)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    run = await store.async_schedule_history_run(SN, "202611", now, guard=lambda: True)
    repeated = await store.async_schedule_history_run(SN, "202611", now, guard=lambda: True)
    assert run == repeated and run.plan_id != baseline.plan_id
    claimed = await store.async_claim_history_page(
        SN, now, 60, plan_id=run.plan_id, guard=lambda: True
    )
    raw = build_record(
        Endpoint.TRAVEL,
        {"times": 0, "list": []},
        now,
        "202611",
        backend_version="0.1.7",
        endpoint_version=run.provider,
    )
    receipt = await store.async_accept_history_page(SN, claimed, raw, guard=lambda: True)
    finished = await store.async_finish_history_baseline(SN, receipt.root, now, guard=lambda: True)
    assert finished.state is HistoryState.COMPLETE
    assert await store.async_history_plan(SN) == baseline
    assert (await store.async_history_run(SN, run.plan_id)).state is HistoryState.COMPLETE
    path = store.path
    await store.async_close()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        assert await reopened.async_history_plan(SN) == baseline
        assert await reopened.async_history_run(SN, run.plan_id) == finished
        assert (
            await reopened.async_history_page(SN, run.plan_id, "202611", 1, guard=lambda: True)
        ).payload()["times"] == 0
    finally:
        await reopened.async_close()


async def test_schema3_migration_preserves_baseline_and_saves_recovery_copy(store):
    path = store.path
    await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    baseline = await store.async_history_plan(SN)
    await store.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE history_pages SET body=(SELECT h.body FROM history_payloads h"
            " WHERE h.digest=history_pages.digest)"
        )
        db.execute("DROP TABLE payload_checks")
        db.execute("DROP TABLE payload_quarantine")
        db.execute("DROP TABLE history_payloads")
        db.execute("PRAGMA user_version=3")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        assert await reopened.async_history_plan(SN) == baseline
        assert (
            await reopened.async_history_page(SN, baseline.plan_id, "202610", 1, guard=lambda: True)
        ).payload()["times"] == 2
        backups = list(path.parent.glob(f"rides.schema3-{source_hash}.sqlite3"))
        assert len(backups) == 1
        with closing(sqlite3.connect(backups[0])) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        await reopened.async_close()


async def test_identical_page_payload_is_stored_once_across_independent_runs(store):
    now = datetime(2026, 11, 1, tzinfo=UTC)
    for _ in range(2):
        run = await store.async_schedule_history_run(SN, "202611", now, guard=lambda: True)
        raw = build_record(
            Endpoint.TRAVEL,
            {"times": 0, "list": []},
            now,
            "202611",
            backend_version="0.1.7",
            endpoint_version=run.provider,
        )
        result = await store.async_accept_history_page(SN, run, raw, guard=lambda: True)
        await store.async_finish_history_baseline(SN, result.root, now, guard=lambda: True)
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM history_payloads").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM history_pages").fetchone()[0] == 2
        assert db.execute("SELECT sum(length(body)) FROM history_pages").fetchone()[0] == 0
    assert (
        await store.async_history_page(SN, run.plan_id, "202611", 1, guard=lambda: True)
    ).payload()["times"] == 0


async def test_reconciliation_coalesces_dirty_requests_and_skips_known_months(store):
    baseline = await store.async_history_plan(SN)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    first = await store.async_reconcile_history_month(
        SN, "202611", now, "a" * 64, False, guard=lambda: True
    )
    assert (
        await store.async_reconcile_history_month(
            SN, "202611", now, "a" * 64, False, guard=lambda: True
        )
        == first
    )
    assert (
        await store.async_reconcile_history_month(
            SN, "202611", now, "b" * 64, False, guard=lambda: True
        )
        == first
    )
    assert len(await store.async_active_history_runs(SN)) == 1
    assert (
        await store.async_reconcile_history_month(
            SN, "202611", now, "b" * 64, True, guard=lambda: True
        )
        is None
    )
    assert not await store.async_active_history_runs(SN)
    assert (await store.async_history_run(SN, first.plan_id)).state is HistoryState.RECONCILED
    assert await store.async_history_plan(SN) == baseline


async def test_history_family_rotation_is_durable_without_budget_reset(store):
    await store.async_set_history_family_turn(2)
    assert await store.async_history_family_turn() == 2
    path = store.path
    await store.async_close()
    restored = RideArchive(path, OWNER)
    try:
        await restored.async_open()
        assert await restored.async_history_family_turn() == 2
    finally:
        await restored.async_close()


async def test_progress_metadata_is_local_persistent_and_success_is_not_last_attempt(store):
    assert (await store.async_history_evidence(SN, guard=lambda: True)).last_completed is None
    first = await store.async_accept_history_page(SN, initial(), record((), 0), guard=lambda: True)
    second = await store.async_accept_history_page(
        SN, first.root, record((), 0, "202609"), guard=lambda: True
    )
    finished = await store.async_finish_history_baseline(SN, second.root, NOW, guard=lambda: True)
    await store.async_request_history(SN, guard=lambda: True)
    await store.async_set_history_paused(SN, True, guard=lambda: True)
    value = await store.async_history_evidence(SN, guard=lambda: True)
    assert value.baseline == finished and value.last_success == NOW
    assert value.health.discovered == value.health.saved == 0
    assert value.paused and value.requested and not value.active
    path = store.path
    await store.async_close()
    reopened = RideArchive(path, OWNER)
    try:
        await reopened.async_open()
        assert await reopened.async_history_evidence(SN, guard=lambda: True) == value
    finally:
        await reopened.async_close()


async def test_successful_page_activity_is_atomic_and_distinct_from_completed_task(
    store, monkeypatch
):
    await store.async_accept_history_page(SN, initial(), record(), guard=lambda: True)
    first = await store.async_history_evidence(SN, guard=lambda: True)
    assert first.last_success == NOW and first.last_completed is None
    expected = await store.async_history_plan(SN)
    second = build_record(
        Endpoint.TRAVEL,
        {"times": 2, "list": [{"travel_id": "ride-two"}]},
        NOW + timedelta(seconds=1),
        "202610",
        backend_version="0.1.7",
        endpoint_version=expected.provider,
    )
    original = store._record_month

    def fail(*args, **kwargs):
        raise ValueError("synthetic transaction failure after activity marker")

    monkeypatch.setattr(store, "_record_month", fail)
    with pytest.raises(ArchiveError):
        await store.async_accept_history_page(SN, expected, second, guard=lambda: True)
    assert (await store.async_history_evidence(SN, guard=lambda: True)).last_success == NOW
    assert (
        await store.async_history_page(SN, expected.plan_id, "202610", 2, guard=lambda: True)
        is None
    )
    monkeypatch.setattr(store, "_record_month", original)
    await store.async_accept_history_page(SN, expected, second, guard=lambda: True)
    after = await store.async_history_evidence(SN, guard=lambda: True)
    assert after.last_success == NOW + timedelta(seconds=1)
    assert after.last_completed is None and after.multi_page_months == 1
    await store._run(lambda: store._check(store._db()))


async def reusable_month(archive, *, month="202608", partial=False, source_before=False):
    from datetime import timedelta

    baseline = HistoryPlan.create("b" * 32, vehicle_key(SN), "202607", month, NOW.isoformat())
    await archive.async_set_history_plan(SN, baseline, None, guard=lambda: True)
    at = NOW + timedelta(seconds=-1 if source_before else 1)
    run = await archive.async_schedule_history_run(SN, month, at, guard=lambda: True)
    for number in range(1, 2 if partial else 3):
        await archive.async_accept_history_page(
            SN,
            run,
            build_record(
                Endpoint.TRAVEL,
                {"times": 2, "list": [{"travel_id": f"reused-{number}", "mileages": number}]},
                NOW + timedelta(seconds=number + 1),
                month,
                backend_version="0.1.7",
                endpoint_version=run.provider,
            ),
            guard=lambda: True,
        )
        run = await archive.async_history_run(SN, run.plan_id)
    return baseline, run


async def test_verified_month_reuse_preserves_original_receipts_ids_budget_and_restart(tmp_path):
    archive = RideArchive(tmp_path / "reuse.sqlite3", OWNER)
    await archive.async_open()
    baseline, source = await reusable_month(archive)
    try:
        before = await archive._run(
            lambda: (
                archive._db()
                .execute("SELECT value FROM meta WHERE key='preservation_budget'")
                .fetchone()
            )
        )
        assert await archive.async_reuse_history_month(
            SN, NOW + timedelta(seconds=10), guard=lambda: True
        )
        evidence = await archive.async_history_evidence(SN, guard=lambda: True)
        assert evidence.last_success == NOW + timedelta(seconds=3)
        assert evidence.last_completed is None  # Local reuse is not a new cloud success.
        assert evidence.health.discovered == 2 and evidence.health.saved == 0
        after = await archive.async_history_plan(SN)
        assert after.next_month == "202607" and after.scanned_months == after.verified_months == 1
        assert (
            after.revision == baseline.revision + 1
            and after.updated_at == (NOW + timedelta(seconds=10)).isoformat()
        )
        for number in (1, 2):
            original = await archive.async_history_page(
                SN, source.plan_id, "202608", number, guard=lambda: True
            )
            copied = await archive.async_history_page(
                SN, baseline.plan_id, "202608", number, guard=lambda: True
            )
            assert copied.received_at == original.received_at
            assert copied.encoded == original.encoded
        snapshot = await archive._run(
            lambda: (
                archive._db()
                .execute("SELECT value FROM meta WHERE key='preservation_budget'")
                .fetchone(),
                archive._db().execute("SELECT count(*) FROM history_payloads").fetchone()[0],
                archive._db()
                .execute("SELECT count(*) FROM history_ids WHERE plan=?", (baseline.plan_id,))
                .fetchone()[0],
            )
        )
        assert snapshot == (before, 2, 2)
        order, turn = await archive.async_preservation_order((SN, "other"))
        assert order[-1] == SN and turn == "detail"
        assert not await archive.async_reuse_history_month(
            SN, NOW + timedelta(seconds=11), guard=lambda: True
        )
        await archive._run(lambda: archive._check(archive._db()))
    finally:
        await archive.async_close()
    reopened = RideArchive(archive.path, OWNER)
    await reopened.async_open()
    assert (await reopened.async_history_plan(SN)) == after
    await reopened.async_close()


@pytest.mark.parametrize(
    "reason",
    ["partial", "recent", "source_before", "newer", "ids", "membership", "size", "pages", "future"],
)
async def test_month_reuse_does_not_turn_unproven_evidence_into_verified_root(
    tmp_path, monkeypatch, reason
):
    from custom_components.ninebot import history_reuse

    archive = RideArchive(tmp_path / "rejected-reuse.sqlite3", OWNER)
    await archive.async_open()
    baseline, source = await reusable_month(
        archive,
        month="202609" if reason == "recent" else "202608",
        partial=reason == "partial",
        source_before=reason == "source_before",
    )
    try:

        def alter():
            db = archive._db()
            with db:
                if reason == "newer":
                    db.execute(
                        "UPDATE months SET observed=?", ((NOW + timedelta(seconds=5)).isoformat(),)
                    )
                elif reason == "ids":
                    db.execute(
                        "UPDATE history_pages SET ids=? WHERE page=1",
                        (encoded({"ids": ["unverified"]}),),
                    )
                elif reason == "membership":
                    db.execute("DELETE FROM membership WHERE ride='reused-1'")

        from custom_components.ninebot.archive_codec import encoded

        await archive._run(alter)
        if reason == "size":
            monkeypatch.setattr(history_reuse, "MAX_BYTES", 1)
        if reason == "pages":
            monkeypatch.setattr(history_reuse, "MAX_PAGES", 1)
        when = NOW + timedelta(seconds=1 if reason == "future" else 10)
        assert not await archive.async_reuse_history_month(SN, when, guard=lambda: True)
        assert await archive.async_history_plan(SN) == baseline
        assert not await archive.async_history_page(
            SN, baseline.plan_id, baseline.end_month, 1, guard=lambda: True
        )
    finally:
        await archive.async_close()


async def test_month_reuse_is_atomic_if_checkpoint_write_fails(tmp_path, monkeypatch):
    from custom_components.ninebot import history_reuse

    archive = RideArchive(tmp_path / "reuse-rollback.sqlite3", OWNER)
    await archive.async_open()
    baseline, _ = await reusable_month(archive)
    try:

        def fail(*args):
            raise ValueError("synthetic checkpoint failure")

        monkeypatch.setattr(history_reuse, "put_root", fail)
        with pytest.raises(ArchiveError):
            await archive.async_reuse_history_month(
                SN, NOW + timedelta(seconds=10), guard=lambda: True
            )
        assert await archive.async_history_plan(SN) == baseline
        assert (
            await archive.async_history_page(SN, baseline.plan_id, "202608", 1, guard=lambda: True)
            is None
        )
        await archive._run(lambda: archive._check(archive._db()))
    finally:
        await archive.async_close()


@pytest.mark.parametrize("reason", ["owner", "paused", "foreign", "corrupt"])
async def test_month_reuse_respects_owner_pause_and_verified_originals(tmp_path, reason):
    archive = RideArchive(tmp_path / "reuse-guards.sqlite3", OWNER)
    await archive.async_open()
    baseline, _ = await reusable_month(archive)
    try:
        when = NOW + timedelta(seconds=10)
        if reason == "owner":
            with pytest.raises(ArchiveError) as err:
                await archive.async_reuse_history_month(SN, when, guard=lambda: False)
            assert err.value.kind is ArchiveFailure.OWNER
        elif reason == "paused":
            await archive.async_set_history_paused(SN, True, guard=lambda: True)
            assert not await archive.async_reuse_history_month(SN, when, guard=lambda: True)
        elif reason == "foreign":
            assert not await archive.async_reuse_history_month("foreign", when, guard=lambda: True)
        else:

            def corrupt():
                db = archive._db()
                with db:
                    db.execute("UPDATE history_payloads SET body=?", (b"invalid-original",))

            await archive._run(corrupt)
            with pytest.raises(ArchiveError) as err:
                await archive.async_reuse_history_month(SN, when, guard=lambda: True)
            assert err.value.kind is ArchiveFailure.OBJECT
        assert await archive.async_history_plan(SN) == baseline
        assert not await archive.async_history_page(
            SN, baseline.plan_id, "202608", 1, guard=lambda: True
        )
    finally:
        await archive.async_close()


async def test_reuse_provenance_rejects_acceptance_before_source_receipt(tmp_path):
    from custom_components.ninebot.archive_codec import decoded, encoded
    from custom_components.ninebot.history_reuse import check

    archive = RideArchive(tmp_path / "reuse-time.sqlite3", OWNER)
    await archive.async_open()
    await reusable_month(archive)
    try:
        await archive.async_reuse_history_month(SN, NOW + timedelta(seconds=10), guard=lambda: True)

        def verify():
            db = archive._db()
            key, value = db.execute(
                "SELECT key,value FROM meta WHERE key LIKE 'history_reuse:%'"
            ).fetchone()
            forged = decoded(value)
            forged["accepted_at"] = NOW.isoformat()
            with pytest.raises(ValueError, match="acceptance time"):
                check(db, key, encoded(forged))

        await archive._run(verify)
    finally:
        await archive.async_close()
