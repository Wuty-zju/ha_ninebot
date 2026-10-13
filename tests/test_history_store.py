"""Real account-local SQL transactions with synthetic private page evidence."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from custom_components.ninebot import history_store
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
        endpoint_version="native-history-v1",
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
        assert len(copies) == 1 and copies[0].read_bytes() == before
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
