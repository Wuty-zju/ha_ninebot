"""Actual background worker and archive bridge, synthetic endpoints only."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_runtime import ArchiveStatistics
from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.history_plan import HistoryState
from custom_components.ninebot.preservation import HistoryPreservation
from custom_components.ninebot.raw import Endpoint, RawStore, build_record

NOW = datetime(2026, 10, 15, tzinfo=UTC)
VEHICLES = ("synthetic-worker-A", "synthetic-worker-B")


def row(identity, month):
    started = datetime.strptime(month + "01", "%Y%m%d").replace(tzinfo=UTC)
    return {
        "travel_id": identity,
        "id": "detail-" + identity,
        "start_time": int(started.timestamp()),
        "end_time": int(started.timestamp()) + 60,
        "duration": 60,
        "mileages": 1,
        "ec": 10,
        "speed": 30,
    }


async def test_full_worker_two_vehicles_restart_retry_and_incremental(hass, tmp_path, monkeypatch):
    hass.config.config_dir = str(tmp_path)
    clock = [NOW]
    monkeypatch.setattr("custom_components.ninebot.preservation.dt_util.utcnow", lambda: clock[0])
    rows = {
        (sn, month): [row(f"{sn}-{month}-{i}", month) for i in range(count)]
        for sn in VEHICLES
        for month, count in (("202610", 2), ("202609", 1))
    }
    calls = []
    failed = False
    statistics = ArchiveStatistics(hass, "synthetic-lifecycle", "synthetic-owner")
    await statistics.async_load()
    co = SimpleNamespace(
        hass=hass,
        config_entry=SimpleNamespace(
            options={
                "preserve_history": True,
                "auto_history_sync": True,
                "history_requests_per_hour": 60,
            }
        ),
        _stopping=False,
        _generation=1,
        _ownership=dict.fromkeys(VEHICLES, 1),
        data=dict.fromkeys(VEHICLES, object()),
        _authenticated=True,
        local_vehicle_available=lambda sn: sn in VEHICLES,
        fresh=lambda sn, group: True,
        raw=RawStore(),
        statistics=statistics,
        async_query_month=AsyncMock(side_effect=AssertionError("Unexpected serve month query")),
    )

    async def query_page(sn, month, page):
        calls.append(("page", sn, month, page))
        found = rows.get((sn, month), [])
        return build_record(
            Endpoint.TRAVEL,
            {
                "times": len(found),
                "total_mileages": len(found),
                "ec": 10 * len(found),
                "list": found[page - 1 : page],
            },
            clock[0],
            month,
            backend_version="0.1.7",
            endpoint_version="0.1.7+native.history.v1",
        )

    async def query_detail(sn, identity, month, **kwargs):
        nonlocal failed
        calls.append(("detail", sn, month, identity))
        if not failed:
            failed = True
            raise NinebotError(ErrorKind.SERVICE, retry_after=120)
        found = next(r for r in rows[(sn, month)] if r["id"] == identity)
        return build_record(
            Endpoint.TRIP_DETAIL,
            {**found, "trail": "120,30,1,1;120.1,30.1,2,2"},
            clock[0],
            month,
            backend_version="0.1.7",
        )

    co.async_query_history_page = query_page
    co.async_query_detail = query_detail
    worker = co.preservation = HistoryPreservation(co)
    try:
        # No preseeded root or known IDs: both default ranges must really enumerate.
        for _ in range(12):
            await worker.async_tick(clock[0])
            clock[0] += timedelta(seconds=61)
        roots = {sn: await statistics.archive.async_history_plan(sn) for sn in VEHICLES}
        assert all(root and not root.discovery_complete for root in roots.values())
        before_restart = list(calls)
        await worker.async_close()
        await statistics.async_close()
        statistics = ArchiveStatistics(hass, "synthetic-lifecycle", "synthetic-owner")
        await statistics.async_load()
        co.statistics = statistics
        worker = co.preservation = HistoryPreservation(co)
        for sn in VEHICLES:
            assert await statistics.archive.async_history_plan(sn) == roots[sn]
        for _ in range(850):
            await worker.async_tick(clock[0])
            clock[0] += timedelta(seconds=61)
            roots = {sn: await statistics.archive.async_history_plan(sn) for sn in VEHICLES}
            if all(root.state is HistoryState.COMPLETE for root in roots.values()):
                break
        assert all(root.state is HistoryState.COMPLETE for root in roots.values())
        for sn, root in roots.items():
            assert root.start_month == "200001" and root.scanned_months == 322
            assert root.verified_months == 322 and not root.gap_months
            health = await statistics.archive.async_history_health(sn, root.plan_id)
            assert health.discovered == health.saved == 3
            assert not (health.missing or health.reference_issues or health.quality_pending)
            assert worker.progress[sn].state == "synced"
            for item in rows[(sn, "202610")] + rows[(sn, "202609")]:
                detail = await statistics.archive.async_private_detail(sn, item["travel_id"])
                assert detail.geometry.payload()["trail"].count(";") == 1
        counts = Counter(calls)
        assert sum(v for key, v in counts.items() if key[0] == "detail") == 7  # Six + one retry.
        assert all(v == 1 for key, v in counts.items() if key[0] == "page")
        assert len([c for c in calls if c[0] == "page"]) == 646
        assert calls[: len(before_restart)] == before_restart
        baseline_ids = {sn: root.plan_id for sn, root in roots.items()}
        stable_detail_calls = Counter(c for c in calls if c[0] == "detail")

        # A live monthly summary reports a new ride but omits older rows. The
        # incremental native run must discover it without re-fetching stable details.
        sn = VEHICLES[0]
        rows[(sn, "202610")].append(row("new-incremental", "202610"))
        summary = travel({"times": 3, "total_mileages": 3, "list": []}, "202610")
        await statistics.async_record(sn, summary, clock[0], "0.1.7")
        for _ in range(20):
            await worker.async_tick(clock[0])
            clock[0] += timedelta(seconds=61)
            if await statistics.archive.async_private_detail(sn, "new-incremental") is not None:
                break
        assert await statistics.archive.async_private_detail(sn, "new-incremental") is not None
        for identity, count in stable_detail_calls.items():
            assert Counter(calls)[identity] == count
        for vehicle in VEHICLES:
            root = await statistics.archive.async_history_plan(vehicle)
            assert root.plan_id == baseline_ids[vehicle] and root.state is HistoryState.COMPLETE
        co.async_query_month.assert_not_awaited()
        assert statistics.error is None
    finally:
        await worker.async_close()
        await statistics.async_close()
