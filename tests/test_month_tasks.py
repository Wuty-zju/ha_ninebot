"""Sparse task persistence, fences, bounded capacity and truthful partial coverage."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.month_tasks import restored
from custom_components.ninebot.preservation import retry_delay
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive

NOW = datetime(2026, 10, 1, tzinfo=UTC)
OWNER = hashlib.sha256(b"synthetic-sparse-owner").hexdigest()


async def test_sparse_dedup_shared_budget_partial_restart_cancel(tmp_path):
    path = tmp_path / "sparse.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        await archive.async_schedule_months("A", ("202601", "202603"), NOW, guard=lambda: True)
        await archive.async_schedule_months("A", ("202601",), NOW, guard=lambda: True)
        assert len(await archive.async_month_tasks("A")) == 2
        claim = await archive.async_claim_month("A", NOW, 30, guard=lambda: True)
        assert claim.month == "202601" and claim.attempts == 1
        # Re-enqueue while in flight does not clobber the claim.
        await archive.async_schedule_months("A", ("202601",), NOW, guard=lambda: True)
        assert await archive.async_finish_month(
            "A", claim, NOW, success=False, reason="service", delay=600, guard=lambda: True
        )
    finally:
        await archive.async_close()
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        assert await archive.async_claim_month("A", NOW, 30, guard=lambda: True) is None
        second = await archive.async_claim_month(
            "A", NOW + timedelta(seconds=121), 30, guard=lambda: True
        )
        assert second.month == "202603"
        await archive.async_finish_month(
            "A",
            second,
            NOW + timedelta(seconds=121),
            success=True,
            complete=False,
            guard=lambda: True,
        )
        stored = await archive.async_month_tasks("A")
        assert stored[1].state == "complete" and stored[1].list_complete is False
        retry = await archive.async_claim_month(
            "A", NOW + timedelta(seconds=601), 30, guard=lambda: True
        )
        assert retry.month == "202601" and retry.attempts == 2
        await archive.async_cancel_months("A", (retry.month,), guard=lambda: True)
        assert not await archive.async_finish_month(
            "A",
            retry,
            NOW + timedelta(seconds=601),
            success=True,
            complete=True,
            guard=lambda: True,
        )
        assert (
            await archive.async_claim_month("A", NOW + timedelta(days=30), 30, guard=lambda: True)
            is None
        )
        assert await archive.async_month_tasks("B") == ()
        with pytest.raises(ArchiveError):
            await archive.async_schedule_months("A", ("202605",), NOW, guard=lambda: False)
    finally:
        await archive.async_close()


async def test_recent_reconciliation_once_per_day_and_finite_failures(tmp_path):
    archive = RideArchive(tmp_path / "recent.sqlite3", OWNER)
    await archive.async_open()
    try:
        await archive.async_schedule_months(
            "A", ("202609",), NOW, source="recent", guard=lambda: True
        )
        task = await archive.async_claim_month("A", NOW, 30, guard=lambda: True)
        await archive.async_finish_month(
            "A", task, NOW, success=True, complete=False, guard=lambda: True
        )
        await archive.async_schedule_months(
            "A", ("202609",), NOW + timedelta(hours=1), source="recent", guard=lambda: True
        )
        assert (
            await archive.async_claim_month("A", NOW + timedelta(hours=1), 30, guard=lambda: True)
            is None
        )
        await archive.async_schedule_months(
            "A", ("202609",), NOW + timedelta(days=1), source="recent", guard=lambda: True
        )
        for attempt in range(8):
            when = NOW + timedelta(days=attempt + 1)
            task = await archive.async_claim_month("A", when, 30, guard=lambda: True)
            await archive.async_finish_month(
                "A", task, when, success=False, reason="protocol", delay=10, guard=lambda: True
            )
        assert (await archive.async_month_tasks("A"))[0].state == "failed"
        await archive.async_schedule_months(
            "A", ("202609",), NOW + timedelta(days=10), source="recent", guard=lambda: True
        )
        assert (
            await archive.async_claim_month("A", NOW + timedelta(days=10), 30, guard=lambda: True)
            is None
        )
        # A new explicit user request can reset a suspended month.
        await archive.async_schedule_months(
            "A", ("202609",), NOW + timedelta(days=11), guard=lambda: True
        )
        assert (
            await archive.async_claim_month("A", NOW + timedelta(days=11), 30, guard=lambda: True)
        ).attempts == 1
    finally:
        await archive.async_close()


async def test_task_capacity_rolls_back_and_retires_metadata_only(tmp_path):
    archive = RideArchive(tmp_path / "capacity.sqlite3", OWNER)
    await archive.async_open()
    months = tuple(f"{2000 + index // 12}{index % 12 + 1:02}" for index in range(129))
    try:
        for index in range(0, 128, 12):
            await archive.async_schedule_months(
                "A", months[index : min(128, index + 12)], NOW, guard=lambda: True
            )
        with pytest.raises(ArchiveError) as err:
            await archive.async_schedule_months("B", (months[-1],), NOW, guard=lambda: True)
        assert err.value.kind is ArchiveFailure.BUSY
        assert await archive.async_month_tasks("B") == ()
        from types import SimpleNamespace

        from custom_components.ninebot.month_sync import SparseMonthSync

        co = SimpleNamespace(
            statistics=SimpleNamespace(archive=archive),
            _generation=1,
            _ownership={"B": 1},
            _stopping=False,
            local_vehicle_available=lambda sn: True,
            fresh=lambda sn, group: True,
        )
        # Automatic seeds cannot deadlock a full queue before existing work drains.
        await SparseMonthSync(co).async_seed_recent(("B",), NOW)
        assert await archive.async_month_tasks("B") == ()
        assert await archive.async_claim_month("A", NOW, 30, guard=lambda: True) is not None
        await archive.async_cancel_months("A", (months[0],), guard=lambda: True)
        await archive.async_schedule_months("B", (months[-1],), NOW, guard=lambda: True)
        assert len(await archive.async_month_tasks("A")) == 127
        assert len(await archive.async_month_tasks("B")) == 1
    finally:
        await archive.async_close()


def test_invalid_task_schema_and_retry_after():
    with pytest.raises(ValueError):
        restored("{}")
    assert retry_delay(1, float("inf")) < 200
    assert retry_delay(1, 600) == 600


async def test_backup_restores_claim_and_owner_scope(tmp_path):
    archive = RideArchive(tmp_path / "source.sqlite3", OWNER)
    await archive.async_open()
    try:
        await archive.async_schedule_months("A", ("202609",), NOW, guard=lambda: True)
        claimed = await archive.async_claim_month("A", NOW, 30, guard=lambda: True)
        await archive.async_backup(tmp_path / "backup.sqlite3")
    finally:
        await archive.async_close()
    restored_archive = RideArchive(tmp_path / "backup.sqlite3", OWNER)
    await restored_archive.async_open()
    try:
        assert (await restored_archive.async_month_tasks("A"))[0] == claimed
        assert await restored_archive.async_claim_month("A", NOW, 30, guard=lambda: True) is None
        invalid = asdict(claimed) | {"next_attempt": float("nan")}
        with pytest.raises(ValueError):
            restored(json.dumps(invalid))
    finally:
        await restored_archive.async_close()
