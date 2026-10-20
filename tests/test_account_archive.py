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
                    "id": "detail-" + identity,
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
    await actor.async_record_profiles((VehicleProfile(sn, "Scooter", "Model"),), NOW, True)
    await actor.async_record_month(sn, month(identity, sn), NOW)
    return actor


async def detail(actor, *, simple=False, at=NOW, identity="ride"):
    summary = (await actor.async_month(SN, "202610")).travel.rides[0]
    raw = {
        "travel_id": identity,
        "id": "detail-" + identity,
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
        assert (current.path.parent / "retired" / legacy.parent.name / "rides.sqlite3").exists()
    finally:
        await restored.async_close()
    await async_prepare_account(hass, UID, 100 * 1024 * 1024)


@pytest.mark.parametrize("older_geometry", [True, False])
async def test_merge_preserves_best_geometry_and_latest_receipt(tmp_path, older_geometry):
    full = await populated(tmp_path / "full" / "rides.sqlite3")
    simple = await populated(tmp_path / "simple" / "rides.sqlite3")
    try:
        await detail(full, at=NOW if older_geometry else NOW + timedelta(seconds=2))
        await detail(simple, simple=True, at=NOW + timedelta(seconds=1))
        source, target = (full, simple) if older_geometry else (simple, full)
        exported = tmp_path / "export.sqlite3"
        await source.async_export_archive(exported)
        await target.async_merge_archive(exported)
        saved = await target.async_private_detail(SN, "ride")
        assert saved.ride.total_track_points == 3
        assert saved.latest.received_at == NOW + timedelta(seconds=1 if older_geometry else 2)
        assert await target.async_merge_archive(exported) == {"rides": 0, "details": 0}
        assert (await target.async_private_detail(SN, "ride")).revision == saved.revision
        assert (target.path.parent / "recovery.sqlite3").is_file()
    finally:
        await full.async_close()
        await simple.async_close()


async def test_vehicle_subset_roundtrip_does_not_leak_other_vehicle(tmp_path):
    actor = await populated(tmp_path / "source" / "rides.sqlite3")
    try:
        await actor.async_record_profiles(
            (VehicleProfile("Other", "Other", "Model"),), NOW + timedelta(seconds=1), False
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
