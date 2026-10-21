"""Actual HA options/upload/download API; synthetic archives, no cloud or browser."""

import asyncio
import hashlib
import io
import json
import re
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import FormData
from homeassistant.setup import async_setup_component

from custom_components.ninebot.archive_options import DOWNLOADS
from custom_components.ninebot.archive_resources import (
    PENDING_EXPORTS,
    ExportBusy,
    export_workspace,
    finish_job,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("legacy_upload", [False, True])
async def test_native_export_download_upload_and_confirm(
    hass, entry, app_client, hass_client, hass_client_no_auth, tmp_path, legacy_upload
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    form = await response.json()
    url = f"/api/config/config_entries/options/flow/{form['flow_id']}"
    fields = {f["name"]: f for f in form["data_schema"]}
    assert fields["retain_history_database"]["default"] is True
    response = await client.post(url, json={"polling_profile": "balanced", "manage_archive": True})
    form = await response.json()
    assert form["step_id"] == "archive"
    response = await client.post(url, json={"next_step_id": "archive_export"})
    form = await response.json()
    choice = form["data_schema"][0]["selector"]["select"]["options"][0]["value"]
    response = await client.post(url, json={"vehicles": [choice]})
    form = await response.json()
    assert form["step_id"] == "archive_download", form
    download = form["description_placeholders"]["url"]
    filename = form["description_placeholders"]["filename"]
    assert 'target="_blank"' in form["description_placeholders"]["download_link"]
    assert re.fullmatch(
        r"ninebot-history_\d{8}T\d{12}Z_1accounts_\d+vehicles_[0-9a-f]{12}_[0-9a-f]{12}\.zip",
        filename,
    )
    # A browser navigation has no Authorization header; the signed path must suffice.
    browser = await hass_client_no_auth()
    response = await browser.get(download)
    assert response.status == 200
    package = await response.read()
    assert package.startswith(b"PK")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Disposition"] == f'attachment; filename="{filename}"'
    with zipfile.ZipFile(io.BytesIO(package)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["export_id"] in filename
        assert manifest["scope_id"] in filename
        assert manifest["created_at"].endswith("+00:00")
        assert manifest["accounts"][0]["schema"] == 7
        assert manifest["accounts"][0]["vehicles"]
    response = await browser.get(download.split("?", 1)[0])
    assert response.status == 401
    if legacy_upload:
        with zipfile.ZipFile(io.BytesIO(package)) as bundle:
            members = {name: bundle.read(name) for name in bundle.namelist()}
        manifest = json.loads(members["manifest.json"])
        record = manifest["accounts"][0]
        database = tmp_path / "schema6.sqlite3"
        database.write_bytes(members[record["file"]])
        with sqlite3.connect(database) as db:
            db.execute("DROP TABLE archive_evidence")
            db.execute("PRAGMA user_version=6")
        members[record["file"]] = database.read_bytes()
        record.update(
            sha256=hashlib.sha256(database.read_bytes()).hexdigest(),
            bytes=database.stat().st_size,
            schema=6,
        )
        members["manifest.json"] = json.dumps(manifest).encode()
        rebuilt = io.BytesIO()
        with zipfile.ZipFile(rebuilt, "w") as bundle:
            for name, value in members.items():
                bundle.writestr(name, value)
        package = rebuilt.getvalue()
    await client.post(url, json={})
    await hass.async_block_till_done()
    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    form = await response.json()
    url = f"/api/config/config_entries/options/flow/{form['flow_id']}"
    await client.post(url, json={"polling_profile": "balanced", "manage_archive": True})
    await client.post(url, json={"next_step_id": "archive_import"})
    data = FormData()
    data.add_field("file", package, filename="history.zip", content_type="application/zip")
    response = await client.post("/api/file_upload", data=data)
    assert response.status == 200
    uploaded = await response.json()
    response = await client.post(url, json={"file": uploaded["file_id"]})
    form = await response.json()
    assert form["step_id"] == "archive_confirm", form
    assert form["errors"] == {}, form
    choice = form["data_schema"][0]["selector"]["select"]["options"][0]["value"]
    response = await client.post(url, json={"vehicles": [choice], "confirm": True})
    form = await response.json()
    assert form["step_id"] == "archive_result", form
    assert not form.get("errors"), form
    await client.post(url, json={})
    await hass.async_block_till_done()
    app_client.async_control.assert_not_awaited()
    # Downloading is bound to its administrator, not just an unguessable URL.
    token = next(iter(hass.data[DOWNLOADS]))
    item = hass.data[DOWNLOADS][token]
    hass.data[DOWNLOADS][token] = ("another-user", item[1])
    response = await client.get(download)
    assert response.status == 404


async def test_archive_options_require_admin(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.ninebot.archive_options.ArchiveOptionsMixin._archive_admin",
        return_value=False,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"polling_profile": "balanced", "manage_archive": True}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "archive_admin_required"


@pytest.mark.parametrize("phase", ["create", "pack"])
async def test_cancel_export_drains_filesystem_before_removing_directory(hass, tmp_path, phase):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    directories = []
    mkdtemp = tempfile.mkdtemp

    def create(**kwargs):
        directory = Path(mkdtemp(dir=tmp_path, **kwargs))
        directories.append(directory)
        if phase == "create":
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
        return str(directory)

    def pack(directory):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        (directory / "synthetic.zip").write_bytes(b"synthetic")

    async def export():
        async with export_workspace(hass, {}) as workspace:
            if phase == "pack":
                await finish_job(hass, pack, workspace.directory)

    with patch("custom_components.ninebot.archive_resources.tempfile.mkdtemp", create):
        task = asyncio.create_task(export())
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert directories[0].exists()
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not directories[0].exists()
    assert not hass.data[PENDING_EXPORTS]


async def test_export_admission_includes_in_progress_work_and_releases_slots(hass):
    downloads = {"existing-download": object()}
    async with AsyncExitStack() as stack:
        for _ in range(3):
            await stack.enter_async_context(export_workspace(hass, downloads))
        with pytest.raises(ExportBusy):
            async with export_workspace(hass, downloads):
                pytest.fail("Exceeded the four-export limit")
    assert not hass.data[PENDING_EXPORTS]
    async with export_workspace(hass, downloads) as workspace:
        directory = workspace.directory
        assert directory.is_dir()
    assert not directory.exists()


async def test_completed_export_transfers_directory_to_download_cleanup(hass):
    async with export_workspace(hass, {}) as workspace:
        directory = workspace.directory
        (directory / "synthetic.zip").write_bytes(b"synthetic")
        workspace.published = True
    assert (directory / "synthetic.zip").exists()
    assert not hass.data[PENDING_EXPORTS]
    # The normal download expiry/HA-stop owner can now remove the completed file.
    (directory / "synthetic.zip").unlink()
    directory.rmdir()


@pytest.mark.parametrize("phase", ["create", "unpack", "open", "confirm"])
async def test_cancel_import_drains_workers_and_releases_owned_workspace(hass, tmp_path, phase):
    from contextlib import ExitStack, nullcontext
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.ninebot.config_flow import NinebotOptionsFlow

    flow = NinebotOptionsFlow()
    flow.hass = hass
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    directories = []
    mkdtemp = tempfile.mkdtemp

    def block():
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)

    def create(**kwargs):
        directory = Path(mkdtemp(dir=tmp_path, **kwargs))
        directories.append(directory)
        if phase == "create":
            block()
        return str(directory)

    def unpack(uploaded, directory):
        if phase == "unpack":
            block()
        directory.mkdir()
        path = directory / "synthetic.sqlite3"
        path.write_bytes(b"synthetic")
        return [("owner", path)]

    async def opened():
        if phase == "open":
            started.set()
            await asyncio.Future()

    async def confirmed():
        started.set()
        await asyncio.Future()

    candidate = MagicMock()
    candidate.async_open = AsyncMock(side_effect=opened)
    candidate.async_close = AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(patch.object(flow, "_archive_admin", AsyncMock(return_value=True)))
        stack.enter_context(
            patch.object(flow, "_archive_confirm", AsyncMock(side_effect=confirmed))
        )
        stack.enter_context(
            patch("custom_components.ninebot.archive_resources.tempfile.mkdtemp", create)
        )
        stack.enter_context(
            patch(
                "custom_components.ninebot.archive_options.process_uploaded_file",
                return_value=nullcontext(tmp_path),
            )
        )
        stack.enter_context(
            patch("custom_components.ninebot.archive_options.unpack_upload", unpack)
        )
        stack.enter_context(
            patch(
                "custom_components.ninebot.archive_options.loaded_accounts",
                return_value={"owner": object()},
            )
        )
        stack.enter_context(patch("custom_components.ninebot.archive_options.readonly"))
        stack.enter_context(
            patch("custom_components.ninebot.archive_options.RideArchive", return_value=candidate)
        )
        task = asyncio.create_task(flow.async_step_archive_import({"file": "synthetic"}))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            if phase in ("create", "unpack"):
                task.cancel()  # Repeated cancellation must not cancel the executor future.
                await asyncio.sleep(0)
                assert not task.done()
                assert directories[0].exists()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not directories[0].exists()
    assert flow._archive_workspace is None and flow._archive_sources is None
    if phase in ("open", "confirm"):
        candidate.async_close.assert_awaited_once()
