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
        json={"poll_interval": 120, "enable_estimation": True, "configure_model": True},
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
    assert entry.options["poll_interval"] == 120
    assert "model_vehicle" not in entry.options
