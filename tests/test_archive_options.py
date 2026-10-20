"""Actual HA options/upload/download API; synthetic archives, no cloud or browser."""

from unittest.mock import patch

import pytest
from aiohttp import FormData
from homeassistant.setup import async_setup_component

from custom_components.ninebot.archive_options import DOWNLOADS

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_native_export_download_upload_and_confirm(hass, entry, app_client, hass_client):
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
    response = await client.post(url, json={"poll_interval": 60, "manage_archive": True})
    form = await response.json()
    assert form["step_id"] == "archive"
    response = await client.post(url, json={"next_step_id": "archive_export"})
    form = await response.json()
    choice = form["data_schema"][0]["selector"]["select"]["options"][0]["value"]
    response = await client.post(url, json={"vehicles": [choice]})
    form = await response.json()
    assert form["step_id"] == "archive_download", form
    download = form["description_placeholders"]["url"]
    response = await client.get(download)
    assert response.status == 200
    package = await response.read()
    assert package.startswith(b"PK")
    assert response.headers["Cache-Control"] == "no-store"
    await client.post(url, json={})
    await hass.async_block_till_done()
    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    form = await response.json()
    url = f"/api/config/config_entries/options/flow/{form['flow_id']}"
    await client.post(url, json={"poll_interval": 60, "manage_archive": True})
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
            result["flow_id"], {"poll_interval": 60, "manage_archive": True}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "archive_admin_required"
