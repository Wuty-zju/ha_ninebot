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
