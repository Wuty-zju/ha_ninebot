"""Exercise the actual serialized options form using an isolated HA HTTP API."""

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_model_device_selection_through_http(hass, entry, app_client, hass_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    assert response.status == 200
    initial = await response.json()
    url = f"/api/config/config_entries/options/flow/{initial['flow_id']}"
    response = await client.post(
        url,
        json={"polling_profile": "slower", "configure_model": True},
    )
    assert response.status == 200
    form = await response.json()
    assert form["step_id"] == "model_vehicle"
    field = form["data_schema"][0]
    assert field["name"] == "model_vehicle"
    assert "selector" in field and "device" in field["selector"]
    assert "options" not in field
    registry = dr.async_get(hass)
    device = next(
        device
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if ("ninebot", "SyntheticSN") in device.identifiers
    )
    other = MockConfigEntry(domain=DOMAIN, unique_id="other-account", data={})
    other.add_to_hass(hass)
    foreign = registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("ninebot", "ForeignSN")}, name="Scooter"
    )
    # A display label or another account's identically named device is not an ID.
    for value in ("Scooter", foreign.id, "missing-device"):
        response = await client.post(url, json={"model_vehicle": value})
        assert response.status == 200
        result = await response.json()
        assert result["errors"] == {"model_vehicle": "model_unavailable"}
        assert result["step_id"] == "model_vehicle"
    registry.async_update_device(device.id, name_by_user="Renamed scooter")
    response = await client.post(url, json={"model_vehicle": device.id})
    assert response.status == 200
    result = await response.json()
    assert result["step_id"] == "model_parameters"
    parameters = {field["name"]: field for field in result["data_schema"]}
    assert parameters["voltage"]["selector"]["number"]["unit_of_measurement"] == "V"
    assert parameters["capacity"]["selector"]["number"]["unit_of_measurement"] == "Ah"
    for invalid in (True, 0, 301, "not-a-number"):
        response = await client.post(url, json={"voltage": invalid, "capacity": 20})
        assert response.status == 400
        assert entry.runtime_data.models.model("SyntheticSN").nominal is None
    response = await client.post(url, json={"voltage": 72, "capacity": 20})
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.runtime_data.models.model("SyntheticSN").nominal == 1.44
    assert entry.options["polling_profile"] == "slower"
    assert "model_vehicle" not in entry.options


async def test_options_cancel_keeps_other_options_and_rated_parameters(
    hass, entry, app_client, hass_client
):
    hass.config_entries.async_update_entry(entry, options={"poll_interval": 300})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "config", {})
    before_options = dict(entry.options)
    client = await hass_client()
    initial = await (
        await client.post(
            "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
        )
    ).json()
    url = f"/api/config/config_entries/options/flow/{initial['flow_id']}"
    await client.post(url, json={"polling_profile": "faster", "configure_model": True})
    device = next(
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in device.identifiers
    )
    response = await client.post(url, json={"model_vehicle": device.id})
    assert response.status == 200 and (await response.json())["step_id"] == "model_parameters"
    before = entry.runtime_data.models.model("SyntheticSN")
    response = await client.delete(url)
    assert response.status == 200
    assert entry.options == before_options
    assert entry.runtime_data.models.model("SyntheticSN") == before


@pytest.mark.parametrize("value", ["", None, [], {}, True, 42])
async def test_empty_or_wrong_type_vehicle_is_field_scoped_not_an_option_enum(
    hass, entry, app_client, hass_client, value
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "config", {})
    before_options = dict(entry.options)
    client = await hass_client()
    initial = await (
        await client.post(
            "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
        )
    ).json()
    url = f"/api/config/config_entries/options/flow/{initial['flow_id']}"
    await client.post(url, json={"polling_profile": "slower", "configure_model": True})
    response = await client.post(url, json={"model_vehicle": value})
    data = await response.json()
    if value == "":
        assert response.status == 200 and data["errors"] == {"model_vehicle": "model_unavailable"}
    else:
        assert response.status == 400
        assert "model_vehicle" in data["errors"]
        assert data["errors"]["model_vehicle"] == "expected str"
        assert "not a valid option" not in data["errors"]["model_vehicle"]
    assert entry.runtime_data.models.model("SyntheticSN").nominal is None
    assert entry.options == before_options


async def test_optional_rated_parameters_roundtrip_and_clear(hass, entry, app_client, hass_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    for values, expected in (
        ({}, (None, None)),
        ({"voltage": 72}, (72, None)),
        ({"capacity": 38}, (None, 38)),
        ({}, (None, None)),
    ):
        initial = await (
            await client.post(
                "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
            )
        ).json()
        url = f"/api/config/config_entries/options/flow/{initial['flow_id']}"
        await client.post(url, json={"configure_model": True})
        response = await client.post(url, json={"model_vehicle": "SyntheticSN"})
        assert response.status == 200
        fields = (await response.json())["data_schema"]
        assert all(not item.get("required") for item in fields)
        response = await client.post(url, json=values)
        assert response.status == 200
        assert (await response.json())["type"] == "create_entry"
        await hass.async_block_till_done()
        model = entry.runtime_data.models.model("SyntheticSN")
        assert (model.voltage, model.capacity) == expected
        assert model.nominal is None


@pytest.mark.parametrize("legacy", [None, 37, 300])
async def test_polling_profile_native_form_preserves_custom_until_selected(
    hass, entry, app_client, hass_client, legacy
):
    from unittest.mock import patch

    if legacy is not None:
        hass.config_entries.async_update_entry(entry, options={"poll_interval": legacy})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    initial = await (
        await client.post(
            "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
        )
    ).json()
    fields = {item["name"]: item for item in initial["data_schema"]}
    assert "poll_interval" not in fields
    profile = fields["polling_profile"]
    assert profile["selector"]["select"]["options"] == [
        "very_fast",
        "faster",
        "balanced",
        "slower",
        "very_slow",
    ]
    assert profile["selector"]["select"]["translation_key"] == "polling_profile"
    if legacy is None:
        assert profile["default"] == "balanced"
    else:
        assert "default" not in profile and not profile["required"]
    url = f"/api/config/config_entries/options/flow/{initial['flow_id']}"
    for invalid in ("custom", "very-fast", 123):
        response = await client.post(url, json={"polling_profile": invalid})
        assert response.status == 400
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        response = await client.post(url, json={})
        assert response.status == 200
        await hass.async_block_till_done()
    if legacy is not None:
        assert entry.options["poll_interval"] == legacy
        assert "polling_profile" not in entry.options
    initial = await (
        await client.post(
            "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
        )
    ).json()
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        response = await client.post(
            f"/api/config/config_entries/options/flow/{initial['flow_id']}",
            json={"polling_profile": "faster"},
        )
        assert response.status == 200
        await hass.async_block_till_done()
    assert entry.options["polling_profile"] == "faster"
    assert "poll_interval" not in entry.options
