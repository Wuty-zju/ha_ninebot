"""Retained archives, private portability and transactional replay; synthetic only."""

import hashlib
import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot import async_remove_entry
from custom_components.ninebot.account_archive import (
    CONF_RETAIN_ARCHIVE,
    account_path,
    async_prepare_account,
    async_remove_account,
    owner_key,
)
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_transfer import pack, readonly, unpack, validate
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import ArchiveError, RideArchive, vehicle_key
from custom_components.ninebot.travel import merge_detail, parse_ride

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 10, tzinfo=UTC)
UID = "synthetic-business"
SN = "SyntheticSN"


def month(identity="ride", sn=SN):
    return travel(
        {
            "times": 1,
            "total_mileages": 1.2,
            "list": [
                {
                    "travel_id": identity,
                    "mileages": 1.2,
                    "duration": 60,
                    "start_time": int(NOW.timestamp()),
                    "end_time": int(NOW.timestamp()) + 60,
                }
            ],
        },
        "202610",
    )


async def populated(path, uid=UID, identity="ride", sn=SN):
    actor = RideArchive(path, owner_key(uid))
    await actor.async_open()
    try:
        await actor.async_record_profiles(
            (VehicleProfile(sn, "Scooter", "Model"),), NOW, complete=True
        )
        await actor.async_record_month(sn, month(identity, sn), NOW)
        return actor
    except BaseException:
        await actor.async_close()
        raise


async def detail(actor, *, simple=False, at=NOW + timedelta(seconds=1), identity="ride"):
    summary = (await actor.async_month(SN, "202610")).travel.rides[0]
    raw = {
        "travel_id": identity,
        "trail": "120,30,1,1;120.001,30.001,2,2" + ("" if simple else ";120.002,30.002,3,3"),
        "is_show_simple_point": simple,
    }
    record = build_record(Endpoint.TRIP_DETAIL, raw, at, "202610", backend_version="0.1.7")
    await actor.async_record_detail(
        SN,
        merge_detail(summary, parse_ride(raw, "202610", source=Endpoint.TRIP_DETAIL)),
        at,
        raw=record,
    )


async def test_default_removal_retains_and_same_account_readdition_reuses(hass, entry, tmp_path):
    path = account_path(hass, UID)
    actor = await populated(path)
    await detail(actor)
    await actor.async_close()
    await async_remove_entry(hass, entry)
    assert path.is_file()
    replacement = MockConfigEntry(domain="ninebot", data={"business_uid": UID}, unique_id=UID)
    assert replacement.entry_id != entry.entry_id
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    restored = RideArchive(path, owner_key(UID))
    try:
        await restored.async_open()
        assert (await restored.async_private_detail(SN, "ride")).ride.total_track_points == 3
    finally:
        await restored.async_close()


async def test_opt_out_deletes_only_own_account(hass, entry):
    first = await populated(account_path(hass, UID))
    other = await populated(account_path(hass, "other"), uid="other")
    await first.async_close()
    await other.async_close()
    hass.config_entries.async_update_entry(entry, options={CONF_RETAIN_ARCHIVE: False})
    await async_remove_entry(hass, entry)
    assert not first.path.parent.exists()
    assert other.path.exists()


async def test_unknown_files_refuse_entire_removal(hass):
    actor = await populated(account_path(hass, UID))
    await actor.async_close()
    actor.path.parent.joinpath("personal.txt").write_text("preserve")
    with pytest.raises(ArchiveError):
        await async_remove_account(hass, UID)
    assert actor.path.is_file()


async def test_legacy_same_owner_merge_and_retire(hass, entry):
    legacy = Path(
        hass.config.path(
            ".storage",
            "ninebot",
            "archives",
            hashlib.sha256(entry.entry_id.encode()).hexdigest(),
            "rides.sqlite3",
        )
    )
    old = await populated(legacy)
    await detail(old)
    await old.async_close()
    current = await populated(account_path(hass, UID), identity="new")
    await current.async_close()
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    restored = RideArchive(current.path, owner_key(UID))
    try:
        await restored.async_open()
        assert len((await restored.async_page(SN)).rides) == 2
        assert await restored.async_private_detail(SN, "ride")
        assert not await hass.async_add_executor_job(legacy.exists)
        assert not (current.path.parent / "retired").exists()
    finally:
        await restored.async_close()
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)


@pytest.mark.parametrize("older_geometry", [True, False])
async def test_merge_preserves_best_geometry_and_latest_receipt(tmp_path, older_geometry):
    full = await populated(tmp_path / "full" / "rides.sqlite3")
    simple = await populated(tmp_path / "simple" / "rides.sqlite3")
    try:
        await detail(full, at=NOW + timedelta(seconds=1 if older_geometry else 3))
        await detail(simple, simple=True, at=NOW + timedelta(seconds=2))
        source, target = (full, simple) if older_geometry else (simple, full)
        exported = tmp_path / "export.sqlite3"
        await source.async_export_archive(exported)
        await target.async_merge_archive(exported)
        saved = await target.async_private_detail(SN, "ride")
        assert saved.ride.total_track_points == 3
        assert saved.latest.received_at == NOW + timedelta(seconds=2 if older_geometry else 3)
        assert await target.async_merge_archive(exported) == {"rides": 0, "details": 0}
        assert (await target.async_private_detail(SN, "ride")).revision == saved.revision
        assert not list(target.path.parent.glob("rides.recovery-*.sqlite3"))
    finally:
        await full.async_close()
        await simple.async_close()


async def test_vehicle_subset_roundtrip_does_not_leak_other_vehicle(tmp_path):
    actor = await populated(tmp_path / "source" / "rides.sqlite3")
    try:
        await actor.async_record_profiles(
            (VehicleProfile("Other", "Other", "Model"),), NOW + timedelta(seconds=1), complete=False
        )
        await actor.async_record_month("Other", month("other", "Other"), NOW)
        exported = tmp_path / "subset.sqlite3"
        await actor.async_export_archive(exported, (vehicle_key(SN),))
        db = readonly(exported)
        try:
            validate(actor, db)
            assert db.execute("SELECT count(*) FROM vehicles").fetchone() == (1,)
            assert db.execute("SELECT count(*) FROM rides").fetchone() == (1,)
        finally:
            db.close()
        package = tmp_path / "history.zip"
        pack(package, [(owner_key(UID), exported)])
        restored = unpack(package, tmp_path / "unpacked")
        assert len(restored) == 1
        destination = await populated(tmp_path / "target" / "rides.sqlite3", identity="new")
        try:
            assert (await destination.async_merge_archive(restored[0][1]))["rides"] == 1
        finally:
            await destination.async_close()
    finally:
        await actor.async_close()


async def test_foreign_account_rejected_without_modifying_target(tmp_path):
    source = await populated(tmp_path / "source" / "rides.sqlite3", uid="foreign")
    target = await populated(tmp_path / "target" / "rides.sqlite3")
    try:
        exported = tmp_path / "export.sqlite3"
        await source.async_export_archive(exported)
        before = target.path.read_bytes()
        with pytest.raises(ArchiveError):
            await target.async_merge_archive(exported)
        assert target.path.read_bytes() == before
    finally:
        await source.async_close()
        await target.async_close()


async def test_failed_merge_rolls_back_and_keeps_recovery(tmp_path):
    source = await populated(tmp_path / "source" / "rides.sqlite3", identity="new")
    target = await populated(tmp_path / "target" / "rides.sqlite3")
    try:
        exported = tmp_path / "export.sqlite3"
        await source.async_export_archive(exported)
        with patch.object(target, "_upsert_ride", side_effect=ValueError("synthetic fault")):
            with pytest.raises(ArchiveError):
                await target.async_merge_archive(exported)
        assert len((await target.async_page(SN)).rides) == 1
        assert (target.path.parent / "recovery.sqlite3").is_file()
        assert (await target.async_merge_archive(exported))["rides"] == 1
    finally:
        await source.async_close()
        await target.async_close()


async def test_backup_fence_blocks_import(tmp_path):
    actor = await populated(tmp_path / "source" / "rides.sqlite3")
    try:
        exported = tmp_path / "export.sqlite3"
        await actor.async_export_archive(exported)
        await actor.async_prepare_backup()
        with pytest.raises(ArchiveError):
            await actor.async_merge_archive(exported)
    finally:
        await actor.async_close()


@pytest.mark.parametrize("fault", ["digest", "duplicate", "traversal", "trigger", "blob"])
async def test_malformed_packages_and_executable_schema_rejected(tmp_path, fault):
    actor = await populated(tmp_path / "source" / "rides.sqlite3")
    try:
        exported = tmp_path / "export.sqlite3"
        await actor.async_export_archive(exported)
        if fault in {"trigger", "blob"}:
            db = sqlite3.connect(exported)
            if fault == "trigger":
                db.execute(
                    "CREATE TRIGGER malicious AFTER INSERT ON rides BEGIN DELETE FROM rides; END"
                )
            else:
                await detail(actor)
                exported.unlink()
                db.close()
                await actor.async_export_archive(exported)
                db = sqlite3.connect(exported)
                db.execute("UPDATE detail_payloads SET body=x'00'")
            db.commit()
            db.close()
            with pytest.raises((ArchiveError, ValueError)):
                await actor.async_merge_archive(exported)
            return
        package = tmp_path / "history.zip"
        pack(package, [(owner_key(UID), exported)])
        with zipfile.ZipFile(package) as original:
            content = {i.filename: original.read(i) for i in original.infolist()}
        manifest = json.loads(content["manifest.json"])
        if fault == "digest":
            manifest["accounts"][0]["sha256"] = "0" * 64
        content["manifest.json"] = json.dumps(manifest).encode()
        broken = tmp_path / "broken.zip"
        with zipfile.ZipFile(broken, "w") as bundle:
            for name, value in content.items():
                bundle.writestr(name, value)
            if fault == "traversal":
                bundle.writestr("../escape", b"bad")
            if fault == "duplicate":
                bundle.writestr("manifest.json", b"{}")
        with pytest.raises(ValueError):
            unpack(broken, tmp_path / "invalid")
        assert not (tmp_path / "escape").exists()
    finally:
        await actor.async_close()


async def test_multiple_account_package_and_standalone_database(tmp_path):
    from custom_components.ninebot.archive_transfer import unpack_upload

    actors = [
        await populated(tmp_path / name / "rides.sqlite3", uid=name) for name in ("first", "second")
    ]
    try:
        paths = []
        for index, actor in enumerate(actors):
            path = tmp_path / f"{index}.sqlite3"
            await actor.async_export_archive(path)
            paths.append((actor.owner, path))
        package = tmp_path / "accounts.zip"
        pack(package, paths)
        sources = unpack_upload(package, tmp_path / "accounts")
        assert {owner for owner, _ in sources} == {a.owner for a in actors}
        standalone = unpack_upload(paths[0][1], tmp_path / "standalone")
        assert standalone[0][0] == actors[0].owner
        db = readonly(standalone[0][1])
        try:
            validate(actors[0], db)
        finally:
            db.close()
    finally:
        for actor in actors:
            await actor.async_close()


async def test_additional_source_evidence_reexports_with_selected_vehicle(tmp_path):
    actor = await populated(tmp_path / "current" / "rides.sqlite3")
    destination = RideArchive(tmp_path / "destination" / "rides.sqlite3", actor.owner)
    try:
        path = tmp_path / "source.sqlite3"
        await actor.async_export_archive(path)
        package = tmp_path / "evidence.zip"
        pack(package, [(actor.owner, path)], {actor.owner: [path]})
        sources = unpack(package, tmp_path / "unpacked")
        assert len(sources) == 2
        await destination.async_open()
        result = await destination.async_merge_archive(
            sources[0][1], (vehicle_key(SN),), (sources[1][1],)
        )
        assert result["rides"] == 1
        assert not (destination.path.parent / "imports").exists()
    finally:
        await actor.async_close()
        await destination.async_close()


async def test_restore_verified_discovery_without_restarting_month_scan(tmp_path):
    from custom_components.ninebot.history_plan import HistoryPlan, HistoryState

    actor = await populated(tmp_path / "source" / "rides.sqlite3")
    destination = RideArchive(tmp_path / "target" / "rides.sqlite3", actor.owner)
    try:
        plan = HistoryPlan.create("a" * 32, vehicle_key(SN), "202610", "202610", NOW.isoformat())
        await actor.async_set_history_plan(SN, plan, None, guard=lambda: True)
        raw = build_record(
            Endpoint.TRAVEL,
            {"times": 1, "list": [{"travel_id": "ride"}]},
            NOW + timedelta(seconds=1),
            "202610",
            backend_version="0.1.7",
            endpoint_version="0.1.7+native.history.v1",
        )
        receipt = await actor.async_accept_history_page(SN, plan, raw, guard=lambda: True)
        assert receipt.root.discovery_complete
        exported = tmp_path / "source.sqlite3"
        await actor.async_export_archive(exported)
        await destination.async_open()
        await destination.async_merge_archive(exported)
        restored = await destination.async_history_plan(SN)
        assert restored.discovery_complete and restored.verified_months == 1
        assert restored.state is HistoryState.PRESERVE
        assert await destination.async_history_page(
            SN, plan.plan_id, "202610", 1, guard=lambda: True
        )
    finally:
        await actor.async_close()
        await destination.async_close()


async def test_deferred_deletion_cannot_erase_readded_account(hass, entry):
    from custom_components.ninebot.backup import async_post_backup, async_pre_backup

    actor = await populated(account_path(hass, UID))
    await actor.async_close()
    hass.config_entries.async_update_entry(entry, options={CONF_RETAIN_ARCHIVE: False})
    await async_pre_backup(hass)
    await async_remove_entry(hass, entry)
    replacement = MockConfigEntry(
        domain="ninebot", unique_id="replacement", data={"business_uid": UID}
    )
    replacement.add_to_hass(hass)
    await async_post_backup(hass)
    assert actor.path.is_file()


async def test_cleanup_consolidates_b105_layout_and_schema6_only_ride(hass, entry):
    """A ride present only in an older snapshot must survive automatic cleanup."""
    canonical = account_path(hass, UID)
    current = await populated(canonical, identity="current")
    await current.async_close()
    paths = [
        canonical.parent / "imports" / ("a" * 64 + ".sqlite3"),
        canonical.parent / "retired" / ("b" * 64) / "rides.sqlite3",
        canonical.parent / "recovery.sqlite3",
        canonical.with_name("rides.schema6-" + "c" * 64 + ".sqlite3"),
    ]
    for index, path in enumerate(paths):
        actor = await populated(path, identity=f"source-{index}")
        await actor.async_close()
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE archive_evidence")
            db.execute("PRAGMA user_version=6")
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    actor = RideArchive(canonical, owner_key(UID))
    try:
        await actor.async_open()
        assert len((await actor.async_page(SN)).rides) == 5
        assert list(canonical.parent.rglob("*.sqlite3")) == [canonical]
    finally:
        await actor.async_close()


async def test_cleanup_failure_keeps_source_and_healthy_account(hass, entry):
    current = await populated(account_path(hass, UID))
    await current.async_close()
    corrupt = current.path.parent / "imports" / ("a" * 64 + ".sqlite3")
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"not sqlite")
    personal = current.path.parent / "personal.sqlite3"
    personal.write_bytes(b"not managed")
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    assert corrupt.read_bytes() == b"not sqlite"
    assert personal.read_bytes() == b"not managed"
    actor = RideArchive(current.path, owner_key(UID))
    try:
        await actor.async_open()
        assert len((await actor.async_page(SN)).rides) == 1
    finally:
        await actor.async_close()


async def test_cleanup_retry_after_commit_does_not_duplicate(hass, entry):
    source = account_path(hass, UID).parent / "retired" / ("a" * 64) / "rides.sqlite3"
    old = await populated(source)
    await detail(old)
    await old.async_close()
    with patch("custom_components.ninebot.account_archive._discard", side_effect=OSError):
        await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    assert source.exists()
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    assert not source.exists()
    actor = RideArchive(account_path(hass, UID), owner_key(UID))
    try:
        await actor.async_open()
        assert len((await actor.async_page(SN)).rides) == 1
        assert (await actor.async_private_detail(SN, "ride")).ride.total_track_points == 3
    finally:
        await actor.async_close()


async def test_old_package_evidence_is_merged_into_single_database(tmp_path):
    first = await populated(tmp_path / "first" / "rides.sqlite3", identity="first")
    second = await populated(tmp_path / "second" / "rides.sqlite3", identity="evidence-only")
    target = RideArchive(tmp_path / "target" / "rides.sqlite3", owner_key(UID))
    try:
        await target.async_open()
        await first.async_export_archive(tmp_path / "first.sqlite3")
        await second.async_export_archive(tmp_path / "second.sqlite3")
        result = await target.async_merge_archive(
            tmp_path / "first.sqlite3", None, (tmp_path / "second.sqlite3",)
        )
        assert result["rides"] == 2
        assert {r.ride_id for r in (await target.async_page(SN)).rides} == {
            "first",
            "evidence-only",
        }
        assert list(target.path.parent.glob("*.sqlite3")) == [target.path]
    finally:
        await first.async_close()
        await second.async_close()
        await target.async_close()


async def test_superseded_detail_originals_survive_without_source_databases(tmp_path):
    target = await populated(tmp_path / "target" / "rides.sqlite3")
    source = await populated(tmp_path / "source" / "rides.sqlite3")
    try:
        await detail(target, at=NOW + timedelta(seconds=1))
        await detail(source, simple=True, at=NOW + timedelta(seconds=2))
        first = tmp_path / "first.sqlite3"
        await source.async_export_archive(first)
        await target.async_merge_archive(first)
        await detail(source, at=NOW + timedelta(seconds=3))
        second = tmp_path / "second.sqlite3"
        await source.async_export_archive(second)
        await target.async_merge_archive(second)
        with sqlite3.connect(target.path) as db:
            assert (
                db.execute("SELECT count(*) FROM archive_evidence WHERE kind='detail'").fetchone()[
                    0
                ]
                == 1
            )
        exported = tmp_path / "exported.sqlite3"
        await target.async_export_archive(exported)
        db = readonly(exported)
        try:
            validate(target, db)
            assert db.execute("SELECT count(*) FROM archive_evidence").fetchone()[0] == 1
        finally:
            db.close()
    finally:
        await target.async_close()
        await source.async_close()


async def test_running_reconstruction_keeps_its_rollback_point(hass, entry):
    path = account_path(hass, UID)
    actor = await populated(path)
    await detail(actor)
    job = await actor.async_reconstruction_job(
        SN, "start", NOW + timedelta(seconds=2), guard=lambda: True
    )
    await actor._run(lambda: actor._reconstruction_backup(vehicle_key(SN), job))
    recovery = path.with_name(f"rides.recovery-{job.job_id}.sqlite3")
    before = recovery.read_bytes()
    await actor.async_close()
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)
    assert recovery.read_bytes() == before


@pytest.mark.parametrize("archive_failure", [False, True])
async def test_removed_entry_clears_credentials_and_entry_stores_but_retains_archive(
    hass, entry, app_client, archive_failure
):
    from homeassistant.helpers.storage import Store

    from custom_components.ninebot import manager_for

    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    directory = manager_for(hass).path(entry.data["session_key"])

    def credentials():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "tokens.json").write_text(json.dumps({"business_uid": UID}))

    await hass.async_add_executor_job(credentials)
    runtime.models.configure(SN, {"voltage": 72})
    await runtime.models.async_save()
    cursor = Store(hass, 1, f"ninebot.{entry.entry_id}.rides_v1", private=True)
    await cursor.async_save({"synthetic": True})
    if archive_failure:
        hass.config_entries.async_update_entry(entry, options={CONF_RETAIN_ARCHIVE: False})
        with patch(
            "custom_components.ninebot.async_remove_account", side_effect=OSError("synthetic")
        ):
            await async_remove_entry(hass, entry)
    else:
        await async_remove_entry(hass, entry)
    assert not await hass.async_add_executor_job(directory.exists)
    assert await runtime.models._store.async_load() is None
    assert await cursor.async_load() is None
    assert await hass.async_add_executor_job(account_path(hass, UID).exists)


async def test_cancel_legacy_copy_drains_before_temporary_cleanup(hass, entry, tmp_path):
    import asyncio
    import tempfile
    import threading
    from unittest.mock import patch

    from custom_components.ninebot.account_archive import _copy_source

    legacy = Path(
        hass.config.path(
            ".storage",
            "ninebot",
            "archives",
            hashlib.sha256(entry.entry_id.encode()).hexdigest(),
            "rides.sqlite3",
        )
    )
    actor = await populated(legacy)
    await actor.async_close()
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    directories = []
    mkdtemp = tempfile.mkdtemp

    def create(**kwargs):
        directory = Path(mkdtemp(dir=tmp_path, **kwargs))
        directories.append(directory)
        return str(directory)

    def copy(source, destination):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        assert destination.parent.exists()
        _copy_source(source, destination)

    with (
        patch("custom_components.ninebot.archive_resources.tempfile.mkdtemp", create),
        patch("custom_components.ninebot.account_archive._copy_source", copy),
    ):
        task = asyncio.create_task(async_prepare_account(hass, UID, 100 * 1024 * 1024))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert directories[0].exists() and not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await hass.async_add_executor_job(legacy.exists)  # Keep unmerged sources.
    assert not directories[0].exists()


@pytest.mark.parametrize("failed_store", ["statistics", "identity", "models", "events"])
async def test_one_entry_store_removal_failure_does_not_skip_other_cleanup(
    hass, entry, app_client, failed_store
):
    from unittest.mock import AsyncMock

    from homeassistant.helpers import issue_registry as ir

    from custom_components.ninebot import manager_for
    from custom_components.ninebot.event_store import RideEventPipeline
    from custom_components.ninebot.identity import IdentityStore
    from custom_components.ninebot.statistics_store import TravelStatisticsStore

    assert await hass.config_entries.async_setup(entry.entry_id)
    operations = {name: AsyncMock() for name in ("statistics", "identity", "models", "events")}
    operations[failed_store].side_effect = OSError("synthetic")
    manager = manager_for(hass)
    with (
        patch.object(TravelStatisticsStore, "async_remove", operations["statistics"]),
        patch.object(IdentityStore, "async_remove", operations["identity"]),
        patch.object(entry.runtime_data.models, "async_remove", operations["models"]),
        patch.object(RideEventPipeline, "async_remove_store", operations["events"]),
        patch.object(manager, "async_remove", new_callable=AsyncMock) as credentials,
    ):
        await async_remove_entry(hass, entry)
    for operation in operations.values():
        operation.assert_awaited_once()
    credentials.assert_awaited_once()
    assert ir.async_get(hass).async_get_issue("ninebot", f"entry_cleanup_{entry.entry_id}")
