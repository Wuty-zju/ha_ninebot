"""Full synthetic client-range discovery; no cloud, sleeps or physical commands."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.ninebot.bootstrap import HistoryBootstrap
from custom_components.ninebot.history_plan import HistoryState, month_count
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import RideArchive

NOW = datetime(2026, 10, 14, tzinfo=UTC)
SN = "synthetic-bootstrap-vehicle"
OWNER = hashlib.sha256(b"synthetic-bootstrap-account").hexdigest()


@pytest.fixture
async def context(tmp_path, monkeypatch):
    archive = RideArchive(tmp_path / "bootstrap.sqlite3", OWNER)
    await archive.async_open()
    clock = [NOW]
    co = SimpleNamespace(
        config_entry=SimpleNamespace(options={"preserve_history": True, "auto_history_sync": True}),
        _stopping=False,
        _generation=1,
        _ownership={SN: 1},
        local_vehicle_available=lambda sn: sn == SN,
        fresh=lambda sn, group: True,
        statistics=SimpleNamespace(
            archive=archive,
            writable=True,
            async_history_page_committed=AsyncMock(),
            record_error=Mock(),
        ),
    )
    calls = []

    async def query(sn, month, page):
        calls.append((sn, month, page))
        rows = []
        total = 0
        if month == "202610":
            total = 2
            rows = [
                {
                    "travel_id": f"ride-{page}",
                    "mileages": 1,
                    "duration": 60,
                    "ec": 10,
                    "start_time": int(NOW.timestamp()) - page * 3600,
                    "end_time": int(NOW.timestamp()) - page * 3600 + 60,
                }
            ]
        return build_record(
            Endpoint.TRAVEL,
            {"times": total, "list": rows},
            clock[0],
            month,
            backend_version="0.1.7",
            endpoint_version="0.1.7+native.history.v1",
        )

    co.async_query_history_page = query
    monkeypatch.setattr("custom_components.ninebot.bootstrap.dt_util.utcnow", lambda: clock[0])
    try:
        yield co, archive, clock, calls
    finally:
        await archive.async_close()


async def test_first_scan_really_enumerates_full_client_range_and_preserves_all_pages(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    root = await archive.async_history_plan(SN)
    assert root.start_month == "200001" and month_count(root.start_month, root.end_month) == 322
    for _ in range(323):
        assert await runner.async_step((SN,), clock[0], 60)
        clock[0] += timedelta(seconds=61)
    root = await archive.async_history_plan(SN)
    assert root.discovery_complete and root.scanned_months == root.verified_months == 322
    assert root.state is HistoryState.PRESERVE  # Details are still missing: no false complete.
    assert len(calls) == 323 and calls[-1] == (SN, "200001", 1)
    assert len((await archive.async_page(SN)).rides) == 2
    assert (
        await archive.async_history_page(SN, root.plan_id, "202610", 2, guard=lambda: True)
    ).payload()["list"][0]["travel_id"] == "ride-2"
    await runner.async_seed((SN,), clock[0])
    assert await archive.async_history_plan(SN) == root
    assert not await runner.async_step((SN,), clock[0], 60)
    assert len(calls) == 323


async def test_shared_budget_prevents_repeated_poll_and_reload_reuses_checkpoint(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    assert await runner.async_step((SN,), NOW, 60)
    assert not await runner.async_step((SN,), NOW, 60)
    assert len(calls) == 1
    root = await archive.async_history_plan(SN)
    assert root.next_page == 2
    await HistoryBootstrap(co).async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) == root


async def test_disabled_or_stale_profiles_do_not_create_or_query_roots(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    co.config_entry.options["auto_history_sync"] = False
    await runner.async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) is None
    assert not await runner.async_step((SN,), NOW, 60)
    co.config_entry.options["auto_history_sync"] = True
    co.fresh = lambda sn, group: False
    await runner.async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) is None
    assert not calls
