"""Official backup boundaries and explicit entry deletion in isolated directories."""

import asyncio
import shutil
import threading
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.ninebot import async_remove_entry, async_setup_entry
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_lifecycle import async_remove_archive
from custom_components.ninebot.archive_runtime import ArchiveStatistics, archive_path
from custom_components.ninebot.backup import (
    ACTORS_KEY,
    async_post_backup,
    async_pre_backup,
    backup_active,
)
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def runtime(hass, entry, app_client):
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data


async def test_backup_freezes_only_writes_restores_and_removal_defers(
    hass, entry, runtime, app_client, tmp_path
):
    archive = runtime.coordinator.statistics.archive
    app_client.async_get_travel.reset_mock()
    await async_pre_backup(hass)
    assert backup_active(hass) and archive.backup_paused
    await archive.async_page("SyntheticSN")  # Reads do not block.
    with pytest.raises(ArchiveError) as err:
        await archive.async_record_month(
            "SyntheticSN", travel({"times": 0, "list": []}, "202609"), dt_util.utcnow()
        )
    assert err.value.kind is ArchiveFailure.BUSY and not archive.write_paused
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)
    copy = tmp_path / "copied.sqlite3"
    await hass.async_add_executor_job(shutil.copyfile, archive.path, copy)
    restored = RideArchive(copy, archive.owner)
    try:
        await restored.async_open()
        assert await restored.async_profiles()
        assert (await restored.async_page("SyntheticSN")).rides == ()
    finally:
        await restored.async_close()
    await async_remove_entry(hass, entry)
    assert archive.path.exists() and runtime.coordinator._stopping
    await async_post_backup(hass)
    assert not backup_active(hass) and not archive.path.parent.exists()
    await async_post_backup(hass)  # Idempotent completion.
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_cancelled_backup_drains_worker_and_thaws(hass, entry, runtime):
    archive = runtime.coordinator.statistics.archive
    begun = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def running():
        loop.call_soon_threadsafe(begun.set)
        assert release.wait(5)

    worker = asyncio.create_task(archive._run(running))
    await begun.wait()
    preparation = asyncio.create_task(async_pre_backup(hass))
    await asyncio.sleep(0)
    preparation.cancel()
    release.set()
    await worker
    with pytest.raises(asyncio.CancelledError):
        await preparation
    assert not backup_active(hass) and not archive.backup_paused
    await archive.async_record_month(
        "SyntheticSN", travel({"times": 0, "list": []}, "202609"), dt_util.utcnow()
    )


async def test_entry_removal_isolated_and_no_follow(hass, entry, runtime, tmp_path):
    archive = runtime.coordinator.statistics.archive
    other = archive_path(hass, "other-entry")
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other account original")
    archive.path.parent.joinpath("notes.txt").write_bytes(b"user original")
    await async_remove_entry(hass, entry)
    assert archive.path.exists() and archive.path.parent.joinpath("notes.txt").exists()
    assert ir.async_get(hass).async_get_issue("ninebot", f"ride_archive_remove_{entry.entry_id}")
    assert other.read_bytes() == b"other account original"

    archive.path.parent.joinpath("notes.txt").unlink()
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"do not remove")
    archive.path.unlink()
    archive.path.symlink_to(outside)
    with pytest.raises(ArchiveError):
        await async_remove_archive(hass, entry.entry_id)
    assert outside.read_bytes() == b"do not remove" and archive.path.is_symlink()
    archive.path.unlink()
    ancestor = archive.path.parent
    ancestor.rmdir()
    ancestor.symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(ArchiveError):
        await async_remove_archive(hass, entry.entry_id)
    assert outside.read_bytes() == b"do not remove"
    ancestor.unlink()
    await async_remove_archive(hass, entry.entry_id)  # Already absent.
    assert other.read_bytes() == b"other account original"


async def test_backup_covers_unopened_and_new_actors_and_preserves_capacity_pause(hass, entry):
    first = ArchiveStatistics(hass, entry.entry_id, "synthetic-business")
    second = None
    try:
        await async_pre_backup(hass)  # The first actor isn't LOADED or open yet.
        await first.async_load()
        assert not first.archive_ready and first.error is ArchiveFailure.BUSY
        assert not first.archive.path.exists()
        second = ArchiveStatistics(hass, "second-entry", "other-business")
        await second.async_load()
        assert not second.archive_ready and not second.archive.path.exists()
        assert second.archive.backup_paused
        first.archive.write_paused = True
        await async_post_backup(hass)
        assert not first.archive.backup_paused and first.archive.write_paused
        assert not second.archive.backup_paused
        assert not ir.async_get(hass).async_get_issue("ninebot", first._archive_issue)
    finally:
        await async_post_backup(hass)
        await first.async_close()
        if second:
            await second.async_close()
    assert not hass.data[ACTORS_KEY]


async def test_actionable_storage_repairs_are_distinct_from_lifecycle(hass, entry):
    store = ArchiveStatistics(hass, entry.entry_id, "synthetic-business")
    try:
        for kind, key in (
            (ArchiveFailure.CAPACITY, "ride_archive_capacity"),
            (ArchiveFailure.STORAGE, "ride_archive_io"),
            (ArchiveFailure.SCHEMA, "ride_archive_invalid"),
            (ArchiveFailure.OWNER, "ride_archive_invalid"),
        ):
            store.record_error(ArchiveError(kind), opening=True)
            issue = ir.async_get(hass).async_get_issue("ninebot", store._archive_issue)
            assert issue.translation_key == key
            ir.async_delete_issue(hass, "ninebot", store._archive_issue)
        for kind in (ArchiveFailure.BUSY, ArchiveFailure.CLOSED, ArchiveFailure.OWNER):
            store.record_error(ArchiveError(kind))
            assert not ir.async_get(hass).async_get_issue("ninebot", store._archive_issue)
    finally:
        await store.async_close()
