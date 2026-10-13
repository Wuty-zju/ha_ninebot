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
            assert db.execute("PRAGMA user_version").fetchone()[0] == 3
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
