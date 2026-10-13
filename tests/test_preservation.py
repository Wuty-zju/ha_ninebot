"""Durable preservation claims and budgets, entirely synthetic/offline."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.preservation import task_state
from custom_components.ninebot.ride_archive import ArchiveError, RideArchive

NOW = datetime(2026, 9, 26, tzinfo=UTC)
OWNER = hashlib.sha256(b"synthetic-account").hexdigest()
SN = "synthetic-vehicle"


def sample():
    return travel(
        {
            "month": "202609",
            "times": 2,
            "total_mileages": 2,
            "list": [
                {
                    "travel_id": identity,
                    "mileages": 1,
                    "duration": 600,
                    "start_time": int(NOW.timestamp()) + index * 1000,
                    "end_time": int(NOW.timestamp()) + index * 1000 + 600,
                }
                for index, identity in enumerate(("first", "second"))
            ],
        },
        "202609",
    )


async def test_claim_budget_retry_and_restart(tmp_path):
    path = tmp_path / "rides.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        await archive.async_record_month(SN, sample(), NOW)
        first = await archive.async_preservation_claim(SN, NOW, 30, guard=lambda: True)
        assert first is not None
        assert await archive.async_preservation_claim(SN, NOW, 30, guard=lambda: True) is None
        with patch("custom_components.ninebot.preservation.random.uniform", return_value=1):
            await archive.async_preservation_finish(
                SN, first, NOW, success=False, retry_after=600, reason="service", guard=lambda: True
            )
    finally:
        await archive.async_close()
    restarted = RideArchive(path, OWNER)
    await restarted.async_open()
    try:
        assert await restarted.async_preservation_claim(SN, NOW, 30, guard=lambda: True) is None
        second = await restarted.async_preservation_claim(
            SN, NOW + timedelta(seconds=121), 30, guard=lambda: True
        )
        assert second is not None and second.ride.ride_id != first.ride.ride_id
        await restarted.async_preservation_finish(
            SN, second, NOW + timedelta(seconds=121), success=True, guard=lambda: True
        )
        retry = await restarted.async_preservation_claim(
            SN, NOW + timedelta(seconds=601), 30, guard=lambda: True
        )
        assert retry.ride.ride_id == first.ride.ride_id and retry.attempts == 2
        await restarted.async_preservation_finish(
            SN, retry, NOW + timedelta(seconds=601), success=True, guard=lambda: True
        )
        assert (
            await restarted.async_preservation_claim(
                SN, NOW + timedelta(days=30), 30, guard=lambda: True
            )
            is None
        )
        with pytest.raises(ArchiveError):
            await restarted.async_preservation_claim(SN, NOW, 30, guard=lambda: False)
    finally:
        await restarted.async_close()


def test_invalid_persistent_task_is_not_silently_recreated():
    with pytest.raises(ValueError):
        task_state("{}")


async def test_worker_uses_background_budget_and_persists_full_detail(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.ninebot.preservation import HistoryPreservation
    from custom_components.ninebot.query_broker import Priority
    from custom_components.ninebot.raw import Endpoint, RawStore, build_record

    archive = RideArchive(tmp_path / "worker.sqlite3", OWNER)
    await archive.async_open()
    try:
        await archive.async_record_month(SN, sample(), NOW)

        async def executor(fn, *args):
            return fn(*args)

        async def save(sn, ride, received, guard, raw=None):
            await archive.async_record_detail(
                sn, ride, received, guard=guard, ordered=True, raw=raw
            )

        result = build_record(
            Endpoint.TRIP_DETAIL,
            {"mileages": 1, "duration": 600, "trail": "120,30,1,1;121,31,2,2"},
            NOW + timedelta(seconds=1),
            "202609",
        )
        co = SimpleNamespace(
            config_entry=SimpleNamespace(
                options={"preserve_history": True, "auto_history_sync": False}
            ),
            _stopping=False,
            _generation=1,
            _ownership={SN: 1},
            data={SN: object()},
            local_vehicle_available=lambda sn: sn == SN,
            fresh=lambda sn, group: True,
            statistics=SimpleNamespace(archive=archive, writable=True, async_record_detail=save),
            raw=RawStore(),
            hass=SimpleNamespace(async_add_executor_job=executor),
            async_query_detail=AsyncMock(return_value=result),
        )
        worker = HistoryPreservation(co)
        await worker.async_tick(NOW)
        assert co.async_query_detail.await_count == 1
        assert co.async_query_detail.await_args.kwargs["priority"] is Priority.BACKGROUND
        identity = co.async_query_detail.await_args.args[1]
        saved = await archive.async_private_detail(SN, identity)
        assert saved.geometry.payload()["trail"] == result.payload()["trail"]
        await worker.async_tick(NOW + timedelta(seconds=1))
        assert co.async_query_detail.await_count == 1
        await worker.async_close()
    finally:
        await archive.async_close()


async def test_persistent_vehicle_order_and_shared_month_budget(tmp_path):
    path = tmp_path / "fair.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        for sn in ("A", "B"):
            await archive.async_record_month(sn, sample(), NOW)
        assert await archive.async_preservation_order(("B", "A")) == (("A", "B"), "detail")
        assert await archive.async_preservation_claim("A", NOW, 30, guard=lambda: True)
        assert not await archive.async_background_budget("B", NOW, 30, guard=lambda: True)
    finally:
        await archive.async_close()
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        assert await archive.async_preservation_order(("A", "B")) == (("B", "A"), "month")
        assert await archive.async_background_budget(
            "B", NOW + timedelta(seconds=121), 30, guard=lambda: True
        )
        assert await archive.async_preservation_order(("A", "B")) == (("A", "B"), "detail")
        assert (
            await archive.async_preservation_claim(
                "A", NOW + timedelta(seconds=122), 30, guard=lambda: True
            )
            is None
        )
    finally:
        await archive.async_close()
