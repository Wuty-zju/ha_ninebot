"""Durable preservation claims and budgets, entirely synthetic/offline."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.preservation import task_state
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive

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
    from unittest.mock import AsyncMock, Mock

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
            _authenticated=True,
            background_ready=lambda *args: True,
            statistics=SimpleNamespace(
                archive=archive,
                writable=True,
                async_record_detail=save,
                archive_ready=True,
                error=None,
                record_error=Mock(),
                _notify_change=Mock(),
            ),
            raw=RawStore(),
            hass=SimpleNamespace(async_add_executor_job=executor),
            async_query_detail=AsyncMock(return_value=result),
        )
        worker = HistoryPreservation(co)
        co.preservation = worker
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


async def settling_sample(tmp_path, *, saved=False):
    from custom_components.ninebot.raw import Endpoint, build_record
    from custom_components.ninebot.travel import merge_detail, parse_ride

    row = {
        "travel_id": "settling",
        "start_time": int(NOW.timestamp()) - 600,
        "end_time": int(NOW.timestamp()),
        "duration": 600,
        "mileages": 1,
    }
    archive = RideArchive(tmp_path / "settling.sqlite3", OWNER)
    await archive.async_open()
    month = travel({"times": 1, "list": [row]}, "202609")
    await archive.async_record_month(SN, month, NOW)

    async def save(payload, at):
        raw = build_record(Endpoint.TRIP_DETAIL, payload, at, "202609")
        incoming = merge_detail(
            month.rides[0], parse_ride(payload, "202609", source=Endpoint.TRIP_DETAIL)
        )
        await archive.async_record_detail(SN, incoming, at, raw=raw)

    if saved:
        await save({**row, "trail": "120,30,1,1;120.1,30.1,2,2"}, NOW + timedelta(seconds=1))
    return archive, row, save


async def test_changed_geometry_does_not_count_as_two_stable_downloads_and_restart_continues(
    tmp_path,
):
    archive, row, save = await settling_sample(tmp_path)
    first = await archive.async_preservation_claim(SN, NOW, 30, guard=lambda: True)
    await save({**row, "trail": "120,30,1,1;120.1,30.1,2,2"}, NOW + timedelta(seconds=1))
    await archive.async_preservation_finish(
        SN, first, NOW + timedelta(seconds=1), success=True, revisit=True, guard=lambda: True
    )
    await archive.async_close()
    restarted = RideArchive(archive.path, OWNER)
    await restarted.async_open()
    try:
        second = await restarted.async_preservation_claim(
            SN, NOW + timedelta(seconds=602), 30, guard=lambda: True
        )
        assert second.observations == 1
        # Same scalar values, more track points: this is not a stable second sample.
        from custom_components.ninebot.raw import Endpoint, build_record
        from custom_components.ninebot.travel import merge_detail, parse_ride

        payload = {**row, "trail": "120,30,1,1;120.1,30.1,2,2;120.2,30.2,3,3"}
        ride = merge_detail(second.ride, parse_ride(payload, "202609", source=Endpoint.TRIP_DETAIL))
        raw = build_record(Endpoint.TRIP_DETAIL, payload, NOW + timedelta(seconds=603), "202609")
        await restarted.async_record_detail(SN, ride, raw.received_at, raw=raw)
        await restarted.async_preservation_finish(
            SN, second, raw.received_at, success=True, revisit=True, guard=lambda: True
        )
        third = await restarted.async_preservation_claim(
            SN, NOW + timedelta(seconds=1204), 30, guard=lambda: True
        )
        assert third is not None and third.observations == 1
        # Same body at a later received timestamp has the same content hash.
        raw = build_record(Endpoint.TRIP_DETAIL, payload, NOW + timedelta(seconds=1205), "202609")
        await restarted.async_record_detail(SN, ride, raw.received_at, raw=raw)
        await restarted.async_preservation_finish(
            SN, third, raw.received_at, success=True, revisit=True, guard=lambda: True
        )
        assert (
            await restarted.async_preservation_claim(
                SN, NOW + timedelta(days=2), 30, guard=lambda: True
            )
            is None
        )
        # A later list correction reopens only this ride's follow-up.
        changed = {**row, "mileages": 2}
        await restarted.async_record_month(
            SN, travel({"times": 1, "list": [changed]}, "202609"), NOW + timedelta(days=2)
        )
        assert (
            await restarted.async_preservation_claim(
                SN, NOW + timedelta(days=2, seconds=1), 30, guard=lambda: True
            )
            is not None
        )
    finally:
        await restarted.async_close()


@pytest.mark.parametrize("recent", [True, False])
async def test_existing_recent_detail_gets_one_followup_but_mature_detail_is_reused(
    tmp_path, recent
):
    archive, _, _ = await settling_sample(tmp_path, saved=True)
    try:
        at = NOW + (timedelta(seconds=2) if recent else timedelta(days=2))
        assert await archive.async_preservation_claim(SN, at, 30, guard=lambda: True) is None
        at += timedelta(seconds=601)
        followup = await archive.async_preservation_claim(SN, at, 30, guard=lambda: True)
        if recent:
            assert followup is not None and followup.observations == 1
            await archive.async_preservation_finish(
                SN, followup, at, success=True, revisit=True, guard=lambda: True
            )
            assert (
                await archive.async_preservation_claim(
                    SN, at + timedelta(hours=1), 30, guard=lambda: True
                )
                is None
            )
        else:
            assert followup is None
    finally:
        await archive.async_close()


async def test_legacy_settled_recent_detail_reopens_once_without_resetting_account_budget(tmp_path):
    import json

    from custom_components.ninebot.ride_archive import vehicle_key

    archive, _, _ = await settling_sample(tmp_path, saved=True)
    try:
        await archive.async_preservation_claim(
            SN, NOW + timedelta(seconds=2), 30, guard=lambda: True
        )

        def legacy():
            db = archive._db()
            key = f"preservation:{vehicle_key(SN)}:settling"
            data = json.loads(
                db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]
            )
            db.execute("DELETE FROM meta WHERE key LIKE 'preservation_detail:%'")
            data.update(observations=2, next_attempt=4102444800)
            with db:
                db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(data), key))

        await archive._run(legacy)
        claim = await archive.async_preservation_claim(
            SN, NOW + timedelta(seconds=602), 30, guard=lambda: True
        )
        assert claim is not None and claim.observations == 1
        assert (
            await archive.async_preservation_claim(
                SN, NOW + timedelta(seconds=603), 30, guard=lambda: True
            )
            is None
        )
        await archive.async_preservation_finish(
            SN, claim, NOW + timedelta(seconds=604), success=True, revisit=True, guard=lambda: True
        )
        assert (
            await archive.async_preservation_claim(
                SN, NOW + timedelta(hours=1), 30, guard=lambda: True
            )
            is None
        )
    finally:
        await archive.async_close()


def test_followup_markers_validate_scope_and_hash():
    from custom_components.ninebot.detail_followup import check, key
    from custom_components.ninebot.ride_archive import vehicle_key

    scope = key(vehicle_key(SN), "settling")
    check(scope, "a" * 64)
    check(scope, "none")
    with pytest.raises(ValueError):
        check(scope, "not-a-digest")
    with pytest.raises(ValueError):
        check("preservation_detail:wrong:ride", "a" * 64)


def test_recent_ride_without_end_remains_eligible_for_followup():
    from custom_components.ninebot.preservation import mutable_detail
    from custom_components.ninebot.travel import parse_ride

    ride = parse_ride({"travel_id": "open", "start_time": int(NOW.timestamp()) - 60}, "202609")
    assert mutable_detail(ride, NOW)
    assert not mutable_detail(ride, NOW + timedelta(days=2))


async def test_detail_uses_current_clock_after_seed_and_publishes_active_phase(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from custom_components.ninebot.preservation import HistoryPreservation
    from custom_components.ninebot.raw import Endpoint, RawStore, build_record

    archive = RideArchive(tmp_path / "clock.sqlite3", OWNER)
    await archive.async_open()
    clock = [NOW]
    monkeypatch.setattr("custom_components.ninebot.preservation.dt_util.utcnow", lambda: clock[0])
    try:
        await archive.async_record_month(SN, sample(), NOW)
        deadline = NOW + timedelta(milliseconds=200)

        def budget():
            with archive._db() as db:
                db.execute(
                    "INSERT INTO meta VALUES ('preservation_budget',?)",
                    (str(deadline.timestamp()),),
                )

        await archive._run(budget)
        # This is the old detail decision: timer timestamp is just before the gate.
        assert await archive.async_preservation_claim(SN, NOW, 60, guard=lambda: True) is None

        async def executor(fn, *args):
            return fn(*args)

        async def save(sn, ride, received, guard, raw=None):
            await archive.async_record_detail(sn, ride, received, guard=guard, raw=raw)

        co = SimpleNamespace(
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
            local_vehicle_available=lambda sn: sn == SN,
            fresh=lambda sn, group: True,
            _authenticated=True,
            background_ready=lambda *args: True,
            statistics=SimpleNamespace(
                archive=archive,
                writable=True,
                async_record_detail=save,
                archive_ready=True,
                error=None,
                record_error=Mock(),
                _notify_change=Mock(),
            ),
            raw=RawStore(),
            hass=SimpleNamespace(async_add_executor_job=executor),
        )
        worker = HistoryPreservation(co)
        co.preservation = worker

        async def seed(*args):
            clock[0] = NOW + timedelta(milliseconds=300)

        worker.bootstrap.async_seed = seed
        worker.incremental.async_seed = AsyncMock()
        worker.months.async_seed_recent = AsyncMock()
        worker._resume_months = AsyncMock(return_value=True)

        async def query(*args, **kwargs):
            assert worker.progress[SN].state == "preserving"
            return build_record(
                Endpoint.TRIP_DETAIL,
                {"mileages": 1, "duration": 600, "trail": "120,30,1,1;120.1,30.1,2,2"},
                clock[0],
                "202609",
            )

        co.async_query_detail = AsyncMock(side_effect=query)
        await worker._step(NOW)
        co.async_query_detail.assert_awaited_once()
        worker._resume_months.assert_not_awaited()
        assert worker._active_query is None
        evidence = await archive.async_history_evidence(SN, guard=lambda: True)
        assert evidence.last_success == clock[0] and evidence.last_completed is None
    finally:
        await archive.async_close()


def aged_row(identity, age, *, duration=600, has_end=True):
    end = NOW - timedelta(days=age)
    row = {
        "travel_id": identity,
        "mileages": 1,
        "duration": duration,
        "start_time": int((end - timedelta(seconds=duration)).timestamp()),
    }
    if has_end:
        row["end_time"] = int(end.timestamp())
    return row


async def record_aged_rows(archive, sn, rows):
    from zoneinfo import ZoneInfo

    from custom_components.ninebot.const import BUSINESS_TIMEZONE

    months = {}
    for row in rows:
        month = (
            datetime.fromtimestamp(row["start_time"], UTC)
            .astimezone(ZoneInfo(BUSINESS_TIMEZONE))
            .strftime("%Y%m")
        )
        months.setdefault(month, []).append(row)
    for month, items in months.items():
        await archive.async_record_month(
            sn, travel({"times": len(items), "list": items}, month), NOW
        )


async def test_risk_band_uses_oldest_confirmed_end_but_keeps_other_history_newest_first(tmp_path):
    archive = RideArchive(tmp_path / "risk-order.sqlite3", OWNER)
    await archive.async_open()
    try:
        await record_aged_rows(
            archive,
            SN,
            [aged_row(f"age-{age}", age) for age in (149, 150, 151, 175, 179, 180, 200)],
        )
        for index, age in enumerate((180, 179, 175, 151, 150, 149, 200)):
            at = NOW + timedelta(seconds=index * 121)
            task = await archive.async_preservation_claim(SN, at, 30, guard=lambda: True)
            assert task.ride.ride_id == f"age-{age}"
            await archive.async_preservation_finish(SN, task, at, success=True, guard=lambda: True)
        assert not await archive.async_preservation_claim(
            SN, NOW + timedelta(hours=1), 30, guard=lambda: True
        )
    finally:
        await archive.async_close()


@pytest.mark.parametrize("count", [3, 130])
async def test_oldest_risk_detail_is_included_before_the_bounded_candidate_limit(tmp_path, count):
    archive = RideArchive(tmp_path / "risk-limit.sqlite3", OWNER)
    await archive.async_open()
    try:
        await record_aged_rows(
            archive,
            SN,
            [
                aged_row(f"risk-{index:03}", 150 + 29 * index / (count - 1))
                for index in range(count)
            ],
        )
        task = await archive.async_preservation_claim(SN, NOW, 30, guard=lambda: True)
        assert task.ride.ride_id == f"risk-{count - 1:03}"
        assert task.ride.ended_at == NOW - timedelta(days=179)
    finally:
        await archive.async_close()


async def test_risk_uses_confirmed_end_not_start_or_inferred_missing_end(tmp_path):
    archive = RideArchive(tmp_path / "risk-ends.sqlite3", OWNER)
    await archive.async_open()
    try:
        await record_aged_rows(
            archive,
            SN,
            [
                aged_row("long-old", 179, duration=20 * 86400),
                aged_row("short-young", 151),
                aged_row("unknown-end", 170, has_end=False),
                aged_row("zeta", 1),
                aged_row("alpha", 1),
            ],
        )
        for index, identity in enumerate(
            ("long-old", "short-young", "alpha", "zeta", "unknown-end")
        ):
            at = NOW + timedelta(seconds=121 * index)
            task = await archive.async_preservation_claim(SN, at, 30, guard=lambda: True)
            assert task.ride.ride_id == identity
            if identity == "unknown-end":
                assert task.ride.ended_at is None
            await archive.async_preservation_finish(SN, task, at, success=True, guard=lambda: True)
    finally:
        await archive.async_close()


async def test_risk_retry_and_vehicle_rotation_survive_restart_without_bypassing_budget(tmp_path):
    path = tmp_path / "risk-restart.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        await record_aged_rows(archive, "A", [aged_row("a-old", 179), aged_row("a-young", 151)])
        await record_aged_rows(archive, "B", [aged_row("b-old", 178)])
        first = await archive.async_preservation_claim("A", NOW, 30, guard=lambda: True)
        assert first.ride.ride_id == "a-old"
        with patch("custom_components.ninebot.preservation.random.uniform", return_value=1):
            await archive.async_preservation_finish(
                "A",
                first,
                NOW,
                success=False,
                retry_after=600,
                reason="service",
                guard=lambda: True,
            )
        assert not await archive.async_preservation_claim("B", NOW, 30, guard=lambda: True)
    finally:
        await archive.async_close()
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        order, _ = await archive.async_preservation_order(("A", "B"))
        assert order == ("B", "A")
        at = NOW + timedelta(seconds=121)
        other = await archive.async_preservation_claim(order[0], at, 30, guard=lambda: True)
        assert other.ride.ride_id == "b-old"
        await archive.async_preservation_finish("B", other, at, success=True, guard=lambda: True)
        assert (await archive.async_preservation_order(("A", "B")))[0] == ("A", "B")
        at += timedelta(seconds=121)
        ready = await archive.async_preservation_claim("A", at, 30, guard=lambda: True)
        assert ready.ride.ride_id == "a-young"  # Oldest is still in persisted backoff.
        await archive.async_preservation_finish("A", ready, at, success=True, guard=lambda: True)
        retry = await archive.async_preservation_claim(
            "A", NOW + timedelta(seconds=601), 30, guard=lambda: True
        )
        assert retry.ride.ride_id == "a-old" and retry.attempts == 2
    finally:
        await archive.async_close()


async def test_saved_mature_risk_detail_is_reused_instead_of_requeried(tmp_path):
    from custom_components.ninebot.raw import Endpoint, build_record

    archive = RideArchive(tmp_path / "risk-reuse.sqlite3", OWNER)
    await archive.async_open()
    try:
        saved = aged_row("saved-risk", 179)
        await record_aged_rows(archive, SN, [saved, aged_row("missing-risk", 151)])
        month = (NOW - timedelta(days=179)).strftime("%Y%m")
        normalized = travel({"times": 1, "list": [saved]}, month).rides[0]
        received = NOW + timedelta(seconds=1)
        raw = build_record(Endpoint.TRIP_DETAIL, saved, received, month)
        assert await archive.async_record_detail(SN, normalized, received, raw=raw)
        task = await archive.async_preservation_claim(
            SN, NOW + timedelta(seconds=2), 30, guard=lambda: True
        )
        assert task.ride.ride_id == "missing-risk"
        detail = await archive.async_private_detail(SN, "saved-risk")
        assert detail.latest.received_at == received and detail.latest.payload() == saved
    finally:
        await archive.async_close()


async def test_detail_and_month_vehicle_rotations_do_not_phase_lock_after_restart(
    tmp_path, monkeypatch
):
    from collections import Counter
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from custom_components.ninebot.preservation import HistoryPreservation
    from custom_components.ninebot.raw import Endpoint, RawStore, build_record

    path = tmp_path / "family-fairness.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    clock = [NOW]
    monkeypatch.setattr("custom_components.ninebot.preservation.dt_util.utcnow", lambda: clock[0])
    for sn in ("A", "B"):
        await record_aged_rows(archive, sn, [aged_row(f"{sn}-{i}", 179 - i) for i in range(8)])
    calls = []

    async def executor(fn, *args):
        return fn(*args)

    async def save(sn, ride, received, guard, raw=None):
        await co.statistics.archive.async_record_detail(
            sn, ride, received, guard=guard, ordered=True, raw=raw
        )

    async def detail(sn, identity, month, **kwargs):
        calls.append(("detail", sn))
        return build_record(
            Endpoint.TRIP_DETAIL,
            {
                "travel_id": identity,
                "duration": 600,
                "mileages": 1,
                "trail": "120,30,1,1;120.1,30.1,2,2",
            },
            clock[0],
            month,
            backend_version="0.1.7",
        )

    co = SimpleNamespace(
        config_entry=SimpleNamespace(
            options={
                "preserve_history": True,
                "auto_history_sync": False,
                "history_requests_per_hour": 60,
            }
        ),
        _stopping=False,
        _generation=1,
        _ownership={"A": 1, "B": 1},
        data={"A": object(), "B": object()},
        _authenticated=True,
        background_ready=lambda *args: True,
        local_vehicle_available=lambda sn: sn in {"A", "B"},
        fresh=lambda sn, group: True,
        raw=RawStore(),
        hass=SimpleNamespace(async_add_executor_job=executor),
        statistics=SimpleNamespace(
            archive=archive,
            writable=True,
            archive_ready=True,
            error=None,
            async_record_detail=save,
            record_error=Mock(),
            _notify_change=Mock(),
        ),
        async_query_detail=AsyncMock(side_effect=detail),
    )

    def worker():
        instance = HistoryPreservation(co)
        co.preservation = instance

        async def pending_months(vehicles, now, budget, **kwargs):
            # A continuously ready month family, using the real account budget.
            # Endpoint acquisition itself is not the subject of this regression.
            for sn in vehicles:
                if await co.statistics.archive.async_background_budget(
                    sn, now, budget, guard=lambda: True
                ):
                    calls.append(("month", sn))
                    return True
            return False

        instance.bootstrap.async_step = pending_months
        return instance

    instance = worker()
    try:
        for batch in range(2):
            for _ in range(4):
                before = len(calls)
                await instance._step(clock[0])
                assert len(calls) == before + 1
                # Neither cursor nor endpoint quota advances on a denied retry.
                state = await archive.async_preservation_order(("A", "B"), kind="detail")
                await instance._step(clock[0])
                assert len(calls) == before + 1
                assert await archive.async_preservation_order(("A", "B"), kind="detail") == state
                clock[0] += timedelta(seconds=61)
            if batch == 0:
                await instance.async_close()
                await archive.async_close()
                archive = RideArchive(path, OWNER)
                await archive.async_open()
                co.statistics.archive = archive
                instance = worker()
        assert Counter(calls) == Counter(
            {("detail", "A"): 2, ("detail", "B"): 2, ("month", "A"): 2, ("month", "B"): 2}
        )
        assert calls[:4] == [("detail", "A"), ("month", "B"), ("detail", "B"), ("month", "A")]
        for sn in ("A", "B"):
            for index in range(2):
                assert await archive.async_private_detail(sn, f"{sn}-{index}") is not None
        co.statistics.record_error.assert_not_called()
        with pytest.raises(ValueError):
            await archive.async_preservation_order(("A", "B"), kind="unknown")
    finally:
        await instance.async_close()
        await archive.async_close()


@pytest.mark.parametrize("key", ["preservation_detail_cursor", "preservation_month_cursor"])
async def test_family_cursor_values_are_validated_without_rewriting_archive(tmp_path, key):
    archive = RideArchive(tmp_path / "invalid-family.sqlite3", OWNER)
    await archive.async_open()

    def corrupt():
        db = archive._db(write=True)
        with db:
            db.execute("INSERT INTO meta VALUES (?,?)", (key, "invalid"))

    await archive._run(corrupt)
    await archive.async_close()
    original = archive.path.read_bytes()
    reopened = RideArchive(archive.path, OWNER)
    try:
        with pytest.raises(ArchiveError) as caught:
            await reopened.async_open()
        assert caught.value.kind is ArchiveFailure.SCHEMA
    finally:
        await reopened.async_close()
    assert archive.path.read_bytes() == original


async def test_account_budget_120_reserves_thirty_second_slots_across_restart(tmp_path):
    archive = RideArchive(tmp_path / "budget.sqlite3", OWNER)
    await archive.async_open()
    try:
        assert await archive.async_background_budget(SN, NOW, 120, guard=lambda: True)
        assert not await archive.async_background_budget(
            "another-vehicle", NOW + timedelta(seconds=29), 120, guard=lambda: True
        )
        assert await archive.async_background_budget(
            "another-vehicle", NOW + timedelta(seconds=30), 120, guard=lambda: True
        )
        with pytest.raises(ValueError):
            await archive.async_background_budget(SN, NOW, 121, guard=lambda: True)
    finally:
        await archive.async_close()
    reopened = RideArchive(tmp_path / "budget.sqlite3", OWNER)
    await reopened.async_open()
    try:
        assert not await reopened.async_background_budget(
            SN, NOW + timedelta(seconds=59), 120, guard=lambda: True
        )
        assert await reopened.async_background_budget(
            SN, NOW + timedelta(seconds=60), 120, guard=lambda: True
        )
    finally:
        await reopened.async_close()


async def test_zero_budget_bypasses_old_hourly_deadline_but_keeps_claim_retry(tmp_path):
    path = tmp_path / "unlimited.sqlite3"
    archive = RideArchive(path, OWNER)
    await archive.async_open()
    try:
        await archive.async_record_month(SN, sample(), NOW)
        assert await archive.async_background_budget(SN, NOW, 1, guard=lambda: True)
        first = await archive.async_preservation_claim(SN, NOW, 0, guard=lambda: True)
        assert first is not None
        second = await archive.async_preservation_claim(SN, NOW, 0, guard=lambda: True)
        assert second is not None and second.ride.ride_id != first.ride.ride_id
        assert await archive.async_preservation_claim(SN, NOW, 0, guard=lambda: True) is None
    finally:
        await archive.async_close()
    restarted = RideArchive(path, OWNER)
    await restarted.async_open()
    try:
        assert await restarted.async_background_budget(SN, NOW, 0, guard=lambda: True)
        assert await restarted.async_background_budget(SN, NOW, 0, guard=lambda: True)
        assert await restarted.async_background_budget(SN, NOW, 60, guard=lambda: True)
        assert not await restarted.async_background_budget(SN, NOW, 60, guard=lambda: True)
        with pytest.raises(ArchiveError):
            await restarted.async_background_budget(SN, NOW, 0, guard=lambda: False)
    finally:
        await restarted.async_close()


@pytest.mark.parametrize(("budget", "seconds"), [(0, 1), (60, 60), (120, 30)])
def test_worker_scheduler_remains_bounded_with_zero_budget(budget, seconds):
    from types import SimpleNamespace

    from custom_components.ninebot.preservation import HistoryPreservation

    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(
            options={"preserve_history": True, "history_requests_per_hour": budget}
        ),
        hass=object(),
    )
    worker = HistoryPreservation(coordinator)
    with patch("custom_components.ninebot.preservation.async_track_time_interval") as track:
        worker.start()
    assert track.call_args.args[2] == timedelta(seconds=seconds)


async def test_archive_close_reaps_worker_before_return(tmp_path):
    archive = RideArchive(tmp_path / "close-worker.sqlite3", OWNER)
    await archive.async_open()
    workers = tuple(archive._executor._threads)
    assert workers and any(worker.is_alive() for worker in workers)
    await archive.async_close()
    assert not any(worker.is_alive() for worker in workers)
    await archive.async_close()
