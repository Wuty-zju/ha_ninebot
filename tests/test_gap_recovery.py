"""Older failed months recover through real worker/SQLite; no cloud or controls."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.ninebot.archive_runtime import ArchiveStatistics
from custom_components.ninebot.history_plan import HistoryPlan, HistoryState
from custom_components.ninebot.history_progress import project_progress
from custom_components.ninebot.preservation import HistoryPreservation
from custom_components.ninebot.raw import Endpoint, RawStore, build_record
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, vehicle_key
from custom_components.ninebot.travel import parse_ride

NOW = datetime(2026, 10, 16, tzinfo=UTC)
SN = "synthetic-old-gap"


def item(identity, month):
    start = datetime(int(month[:4]), int(month[4:]), 1, tzinfo=UTC)
    return {
        "travel_id": identity,
        "mileages": 1,
        "duration": 60,
        "ec": 10,
        "start_time": int(start.timestamp()),
        "end_time": int(start.timestamp()) + 60,
    }


def page(month, now, rows, total):
    return build_record(
        Endpoint.TRAVEL,
        {
            "times": total,
            "list": rows,
            "total_mileages": total,
            "ec": total * 10,
            "duration": total * 60,
        },
        now,
        month,
        backend_version="0.1.7",
        endpoint_version="0.1.7+native.history.v1",
    )


async def saved_detail(archive, sn, row, month, now):
    body = {**row, "trail": "120,30,1,1;120.01,30.01,2,2"}
    raw = build_record(Endpoint.TRIP_DETAIL, body, now, month, backend_version="0.1.7")
    await archive.async_record_detail(
        sn,
        parse_ride(body, month, source=Endpoint.TRIP_DETAIL),
        now,
        raw=raw,
        ordered=True,
        guard=lambda: True,
    )


async def baseline(archive, sn, identity, *, gaps=("202608",)):
    root = HistoryPlan.create(
        identity, vehicle_key(sn), "202608", "202610", (NOW - timedelta(seconds=5)).isoformat()
    )
    await archive.async_set_history_plan(sn, root, None, guard=lambda: True)
    for index, month in enumerate(("202610", "202609", "202608")):
        rows, total = ([item(sn + "-stable", month)], 1) if month == "202609" else ([], 0)
        if month in gaps:
            rows, total = [], 2  # Empty nonzero page: retain an explicit unverified gap.
        receipt = await archive.async_accept_history_page(
            sn, root, page(month, NOW + timedelta(seconds=index), rows, total), guard=lambda: True
        )
        root = receipt.root
    if "202609" not in gaps:
        await saved_detail(
            archive, sn, item(sn + "-stable", "202609"), "202609", NOW + timedelta(seconds=3)
        )
    root = await archive.async_finish_history_baseline(
        sn, root, NOW + timedelta(seconds=4), guard=lambda: True
    )
    assert root.state is HistoryState.WITH_GAPS and root.gap_months == len(gaps)
    return root


@pytest.fixture
async def context(hass, tmp_path, monkeypatch):
    hass.config.config_dir = str(tmp_path)
    statistics = ArchiveStatistics(hass, "synthetic-old-gap-entry", "synthetic-old-gap-owner")
    await statistics.async_load()
    root = await baseline(statistics.archive, SN, "a" * 32)
    clock = [NOW + timedelta(seconds=5)]
    monkeypatch.setattr("custom_components.ninebot.preservation.dt_util.utcnow", lambda: clock[0])
    calls = []
    fail = [False]
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
        _ownership={SN: 1},
        data={SN: object()},
        _authenticated=True,
        background_ready=lambda: True,
        local_vehicle_available=lambda sn: sn in co.data,
        fresh=lambda *a: True,
        raw=RawStore(),
        statistics=statistics,
        async_query_month=AsyncMock(side_effect=AssertionError("Unexpected serve refresh")),
    )

    async def query(sn, month, number):
        calls.append(("page", sn, month, number))
        assert month == "202608"  # Stable Sept/Oct must not be scanned again.
        rows = [item(sn + "-gap-a", month), item(sn + "-gap-b", month)]
        return page(month, clock[0], [] if fail[0] else rows[number - 1 : number], 2)

    async def detail(sn, identity, month, **kwargs):
        calls.append(("detail", sn, month, identity))
        assert "stable" not in identity
        body = {**item(identity, month), "trail": "120,30,1,1;120.01,30.01,2,2"}
        return build_record(Endpoint.TRIP_DETAIL, body, clock[0], month, backend_version="0.1.7")

    co.async_query_history_page, co.async_query_detail = query, detail
    co.preservation = HistoryPreservation(co)
    try:
        yield co, root, clock, calls, fail
    finally:
        await co.preservation.async_close()
        await co.statistics.async_close()


async def finish(co, clock, limit=30):
    for _ in range(limit):
        await co.preservation.async_tick(clock[0])
        clock[0] += timedelta(seconds=61)
        if co.preservation.progress.get(SN) and co.preservation.progress[SN].state == "synced":
            return
    raise AssertionError(co.preservation.progress)


async def test_old_gap_actual_pages_new_details_and_effective_progress_preserve_originals(context):
    co, original, clock, calls, _ = context
    archive = co.statistics.archive
    with closing(sqlite3.connect(archive.path)) as db:
        before = db.execute(
            "SELECT month,page,digest FROM history_pages WHERE plan=? ORDER BY month,page",
            (original.plan_id,),
        ).fetchall()
        raw_before = db.execute(
            "SELECT digest,body FROM history_payloads ORDER BY digest"
        ).fetchall()
    await finish(co, clock)
    assert await archive.async_history_plan(SN) == original  # Historical root is never forged.
    value = co.preservation.progress[SN]
    assert value.scan_percent == value.detail_percent == 100
    assert value.initial_gap_months == value.resolved_gap_months == 1 and value.gap_months == 0
    assert (
        value.discovered == value.saved == 3
    )  # Includes new resolution IDs, not just baseline one.
    assert [(c[2], c[3]) for c in calls if c[0] == "page"] == [("202608", 1), ("202608", 2)]
    assert len([c for c in calls if c[0] == "detail"]) == 2
    assert not co.async_query_month.await_count
    with closing(sqlite3.connect(archive.path)) as db:
        assert (
            db.execute(
                "SELECT month,page,digest FROM history_pages WHERE plan=? ORDER BY month,page",
                (original.plan_id,),
            ).fetchall()
            == before
        )
        for digest, body in raw_before:
            assert db.execute(
                "SELECT body FROM history_payloads WHERE digest=?", (digest,)
            ).fetchone() == (body,)
    view = await co.statistics.async_view(SN, ("202608",))
    assert len(view.month_rides(SN, "202608")[0]) == 2
    assert view.month_rides(SN, "202608")[1]
    start, end = datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
    local = await archive.async_timeline(SN, start, end)
    assert len(local.records) == 2
    for record in local.records:
        detail = await archive.async_private_detail(SN, record.ride.ride_id)
        assert len(detail.ride.track_points) == 2
    old_calls = list(calls)
    clock[0] += timedelta(days=8)
    for _ in range(3):
        clock[0] += timedelta(seconds=61)
        await co.preservation.async_tick(clock[0])
    assert calls == old_calls  # A resolved stable month has no periodic cloud scan.


async def test_failed_gap_retry_survives_restart_and_does_not_spin(context):
    co, original, clock, calls, fail = context
    fail[0] = True
    for _ in range(5):
        await co.preservation.async_tick(clock[0])
        clock[0] += timedelta(seconds=61)
    assert len(calls) == 1 and calls[0][0] == "page"
    assert co.preservation.progress[SN].state == "waiting_retry"
    assert co.preservation.progress[SN].gap_months == 1
    path = co.statistics.archive.path
    await co.preservation.async_close()
    await co.statistics.async_close()
    co.statistics = ArchiveStatistics(co.hass, "synthetic-old-gap-entry", "synthetic-old-gap-owner")
    await co.statistics.async_load()
    co.preservation = HistoryPreservation(co)
    assert co.statistics.archive.path == path
    for _ in range(3):
        await co.preservation.async_tick(clock[0])
        clock[0] += timedelta(seconds=61)
    assert len(calls) == 1
    fail[0] = False
    clock[0] += timedelta(hours=7)
    await finish(co, clock)
    assert len([c for c in calls if c[0] == "page"]) == 3
    assert await co.statistics.archive.async_history_plan(SN) == original


async def test_manual_complete_pages_resolve_immediately_but_detail_gap_blocks_success(context):
    co, original, clock, calls, _ = context
    archive = co.statistics.archive
    run = await archive.async_schedule_history_run(SN, "202608", clock[0], guard=lambda: True)
    joined = await archive.async_seed_history_gap(SN, clock[0], guard=lambda: True)
    assert joined == run
    rows = [item(SN + "-manual", "202608")]
    receipt = await archive.async_accept_history_page(
        SN, run, page("202608", clock[0], rows, 1), guard=lambda: True
    )
    evidence = await archive.async_history_evidence(SN, guard=lambda: True)
    assert evidence.gaps.resolved == 1 and evidence.gaps.pending == 0
    assert evidence.health.discovered == 2 and evidence.health.missing == 1
    progress = project_progress(evidence, clock[0], enabled=True, paused=False, authenticated=True)
    assert progress.state != "synced" and progress.detail_percent == 50
    await saved_detail(archive, SN, rows[0], "202608", clock[0] + timedelta(seconds=1))
    await archive.async_finish_history_baseline(
        SN, receipt.root, clock[0] + timedelta(seconds=2), guard=lambda: True
    )
    await co.preservation.async_refresh_progress((SN,), clock[0] + timedelta(seconds=3))
    assert co.preservation.progress[SN].state == "synced"
    assert not calls and await archive.async_history_plan(SN) == original


async def test_opt_out_pause_and_owner_loss_do_not_register_gap_work(context):
    co, _, clock, calls, _ = context
    archive = co.statistics.archive
    co.config_entry.options["auto_history_sync"] = False
    await co.preservation.async_tick(clock[0])
    assert not calls and not await archive.async_active_history_runs(SN)
    co.config_entry.options["auto_history_sync"] = True
    await archive.async_set_history_paused(SN, True, guard=lambda: True)
    assert await archive.async_seed_history_gap(SN, clock[0], guard=lambda: True) is None
    await archive.async_set_history_paused(SN, False, guard=lambda: True)
    with pytest.raises(ArchiveError) as error:
        await archive.async_seed_history_gap(SN, clock[0], guard=lambda: False)
    assert error.value.kind is ArchiveFailure.OWNER
    assert not await archive.async_active_history_runs(SN)


async def test_local_damage_does_not_certify_recovery_or_trigger_cloud_overwrite(context):
    co, _, clock, calls, _ = context
    archive = co.statistics.archive
    await finish(co, clock)
    before_calls = list(calls)
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("DELETE FROM history_ids WHERE ride=?", (SN + "-gap-a",))
    await co.preservation.async_refresh_progress((SN,), clock[0])
    assert co.preservation.progress[SN].state != "synced"
    assert co.preservation.progress[SN].gap_months == 1
    clock[0] += timedelta(days=8)
    await co.preservation.async_tick(clock[0])
    assert calls == before_calls


async def test_two_vehicle_gap_work_uses_same_budget_and_persistent_family_fairness(context):
    co, _, clock, calls, _ = context
    archive = co.statistics.archive
    other = "synthetic-old-gap-two"
    await baseline(archive, other, "b" * 32)
    co.data[other] = object()
    co._ownership[other] = 1
    for _ in range(30):
        await co.preservation.async_tick(clock[0])
        clock[0] += timedelta(seconds=61)
        if len(co.preservation.progress) == 2 and all(
            v.state == "synced" for v in co.preservation.progress.values()
        ):
            break
    assert all(v.state == "synced" for v in co.preservation.progress.values())
    assert len([c for c in calls if c[0] == "page"]) == 4
    assert len([c for c in calls if c[0] == "detail"]) == 4
    old_calls = list(calls)
    await co.preservation.async_tick(clock[0] - timedelta(seconds=60))
    assert calls == old_calls


async def test_fully_enumerated_recovery_keeps_server_degradation_visible(context):
    co, _, clock, _, _ = context
    original = co.async_query_detail

    async def degraded(sn, identity, month, **kwargs):
        raw = await original(sn, identity, month, **kwargs)
        return build_record(
            Endpoint.TRIP_DETAIL,
            {**raw.payload(), "is_show_simple_point": True},
            raw.received_at,
            month,
            backend_version=raw.backend_version,
        )

    co.async_query_detail = degraded
    for _ in range(30):
        await co.preservation.async_tick(clock[0])
        clock[0] += timedelta(seconds=61)
        value = co.preservation.progress.get(SN)
        if (
            value
            and value.resolved_gap_months == 1
            and value.missing == 0
            and not value.active_months
        ):
            break
    value = co.preservation.progress[SN]
    assert value.resolved_gap_months == 1 and value.gap_months == 0
    assert value.scan_percent == value.detail_percent == 100
    assert value.state == "with_gaps" and value.simplified == 2


async def test_multiple_gap_months_rotate_and_keep_retry_schedule_after_restart(context):
    from custom_components.ninebot.ride_archive import RideArchive

    co, _, clock, calls, _ = context
    archive = co.statistics.archive
    other = "synthetic-many-gaps"
    root = await baseline(archive, other, "c" * 32, gaps=("202608", "202609"))
    runs = [
        await archive.async_seed_history_gap(other, clock[0], guard=lambda: True) for _ in range(2)
    ]
    assert [run.start_month for run in runs] == ["202609", "202608"]
    for run in runs:
        failed = await archive.async_accept_history_page(
            other, run, page(run.start_month, clock[0], [], 2), guard=lambda: True
        )
        await archive.async_finish_history_baseline(
            other, failed.root, clock[0], guard=lambda: True
        )
    assert await archive.async_seed_history_gap(other, clock[0], guard=lambda: True) is None
    path, owner = archive.path, archive.owner
    await co.preservation.async_close()
    await co.statistics.async_close()
    reopened = RideArchive(path, owner)
    try:
        await reopened.async_open()
        assert await reopened.async_seed_history_gap(other, clock[0], guard=lambda: True) is None
        later = clock[0] + timedelta(hours=7)
        retry = [
            await reopened.async_seed_history_gap(other, later, guard=lambda: True)
            for _ in range(2)
        ]
        assert [run.start_month for run in retry] == ["202609", "202608"]
        assert all(a.plan_id != b.plan_id for a, b in zip(runs, retry, strict=True))
        assert await reopened.async_history_plan(other) == root
        assert not calls
    finally:
        await reopened.async_close()


async def test_full_run_queue_does_not_leave_an_untracked_gap_attempt(context):
    co, root, clock, calls, _ = context
    archive = co.statistics.archive
    for number in range(1, 13):
        await archive.async_schedule_history_run(
            SN, f"2010{number:02}", clock[0], guard=lambda: True
        )
    assert await archive.async_seed_history_gap(SN, clock[0], guard=lambda: True) is None
    with closing(sqlite3.connect(archive.path)) as db:
        assert not db.execute("SELECT key FROM meta WHERE key LIKE 'history_gap:%'").fetchall()
    assert await archive.async_history_plan(SN) == root and not calls


async def test_superseded_failed_attempt_ids_remain_in_detail_and_reference_denominator(context):
    co, _, clock, calls, _ = context
    archive = co.statistics.archive
    attempt = await archive.async_seed_history_gap(SN, clock[0], guard=lambda: True)
    first = await archive.async_accept_history_page(
        SN,
        attempt,
        page("202608", clock[0], [item("partial-old-id", "202608")], 2),
        guard=lambda: True,
    )
    failed = await archive.async_accept_history_page(
        SN, first.root, page("202608", clock[0], [], 2), guard=lambda: True
    )
    assert failed.root.gap_months == 1
    later = clock[0] + timedelta(hours=7)
    replacement = await archive.async_seed_history_gap(SN, later, guard=lambda: True)
    assert replacement.plan_id != attempt.plan_id
    good = await archive.async_accept_history_page(
        SN,
        replacement,
        page("202608", later, [item("later-good-id", "202608")], 1),
        guard=lambda: True,
    )
    assert good.verified
    await saved_detail(archive, SN, item("later-good-id", "202608"), "202608", later)
    await archive.async_finish_history_baseline(SN, good.root, later, guard=lambda: True)
    evidence = await archive.async_history_evidence(SN, guard=lambda: True)
    assert evidence.gaps.resolved == 1
    assert (
        evidence.health.discovered == 3
        and evidence.health.saved == 2
        and evidence.health.missing == 1
    )
    progress = project_progress(evidence, later, enabled=True, paused=False, authenticated=True)
    assert progress.state != "synced" and progress.detail_percent == 66
    await saved_detail(archive, SN, item("partial-old-id", "202608"), "202608", later)
    evidence = await archive.async_history_evidence(SN, guard=lambda: True)
    assert evidence.health.discovered == evidence.health.saved == 3
    assert (
        project_progress(evidence, later, enabled=True, paused=False, authenticated=True).state
        == "synced"
    )
    assert not calls


async def test_full_queue_can_replace_same_month_failed_discovery_without_extra_slot(context):
    from custom_components.ninebot.ride_archive import RideArchive

    co, root, clock, calls, _ = context
    archive = co.statistics.archive
    attempt = await archive.async_seed_history_gap(SN, clock[0], guard=lambda: True)
    partial = item("full-queue-old-id", "202608")
    first = await archive.async_accept_history_page(
        SN, attempt, page("202608", clock[0], [partial], 2), guard=lambda: True
    )
    failed = await archive.async_accept_history_page(
        SN, first.root, page("202608", clock[0], [], 2), guard=lambda: True
    )
    assert failed.root.discovery_complete and failed.root.gap_months == 1
    assert failed.root.state is HistoryState.PRESERVE
    for number in range(1, 12):
        await archive.async_schedule_history_run(
            SN, f"2010{number:02}", clock[0], guard=lambda: True
        )
    assert len(await archive.async_active_history_runs(SN)) == 12
    # Cooling attempts do not create another run, even with a full queue.
    assert await archive.async_seed_history_gap(SN, clock[0], guard=lambda: True) is None
    later = clock[0] + timedelta(hours=7)
    replacement = await archive.async_seed_history_gap(SN, later, guard=lambda: True)
    assert replacement is not None and replacement.plan_id != attempt.plan_id
    active = await archive.async_active_history_runs(SN)
    assert len(active) == 12 and replacement in active
    assert all(run.plan_id != attempt.plan_id for run in active)
    with pytest.raises(ArchiveError) as err:
        await archive.async_schedule_history_run(SN, "201012", later, guard=lambda: True)
    assert err.value.kind is ArchiveFailure.BUSY
    with closing(sqlite3.connect(archive.path)) as db:
        from custom_components.ninebot.incremental_runs import get

        assert get(db, vehicle_key(SN), attempt.plan_id) == failed.root
    health = await archive.async_history_health(SN, root.plan_id)
    assert health.discovered == 2 and health.missing == 1
    assert await archive.async_history_plan(SN) == root
    path, owner = archive.path, archive.owner
    await co.preservation.async_close()
    await co.statistics.async_close()
    reopened = RideArchive(path, owner)
    try:
        await reopened.async_open()
        active = await reopened.async_active_history_runs(SN)
        assert len(active) == 12 and replacement in active
        health = await reopened.async_history_health(SN, root.plan_id)
        assert health.discovered == 2 and health.missing == 1
        assert await reopened.async_seed_history_gap(SN, later, guard=lambda: True) is None
    finally:
        await reopened.async_close()
    assert not calls
