"""Small installed-Core API canary, independent of the generated HA test plugin."""

import importlib
import json
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace


def main() -> None:
    """Exercise our native adapters without creating an account or starting a client."""
    from homeassistant import data_entry_flow
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from custom_components.ninebot.client import response_data
    from custom_components.ninebot.compat import conditional_field, device_identifiers, validation
    from custom_components.ninebot.const import PLATFORMS
    from custom_components.ninebot.entity import NinebotEntity
    from custom_components.ninebot.exceptions import ErrorKind, NinebotError
    from custom_components.ninebot.models import VehicleProfile

    config = json.loads((Path(__file__).parents[1] / ".github/ci.json").read_text())
    assert version("homeassistant") == config["native_boundary"]["ha"]
    for platform in PLATFORMS:
        importlib.import_module(f"custom_components.ninebot.{platform}")
    info = NinebotEntity.device_info.fget(
        SimpleNamespace(
            sn="synthetic-vehicle",
            snapshot=SimpleNamespace(profile=VehicleProfile("synthetic-vehicle", "Scooter", "M")),
            entry=SimpleNamespace(runtime_data=SimpleNamespace(identities=None)),
        )
    )
    assert isinstance(info, DeviceInfo)
    assert device_identifiers(info) == {("ninebot", "synthetic-vehicle")}
    assert device_identifiers({"identifiers": {("ninebot", "synthetic-vehicle")}}) == (
        device_identifiers(info)
    )
    marker = conditional_field("selection", [], when="enabled")
    assert marker.visible == {"field": "enabled", "value": True}
    schema = validation.Schema({validation.Optional("enabled", default=False): bool, marker: [str]})
    serialized = [{"name": "enabled"}, {"name": "selection"}]
    data_entry_flow.add_visible_conditions_to_serialized_schema(schema, serialized)
    assert serialized[1]["visible"] == marker.visible
    # Verify the pinned Core's omission contract, without adding production
    # dependencies on this internal validation helper.
    stripped, submitted = data_entry_flow._strip_hidden_fields(
        schema, {"enabled": False, "selection": ["not-submitted"]}
    )
    assert stripped(submitted) == {"enabled": False}
    assert schema({"enabled": True, "selection": ["saved"]})["selection"] == ["saved"]
    assert callable(DataUpdateCoordinator.async_contexts)
    assert callable(getattr(data_entry_flow, "Optional", None))
    try:
        response_data(502, {"ok": False, "error": {"code": "upstream_error"}})
    except NinebotError as error:
        assert error.kind is ErrorKind.SERVICE
        assert error.context.proxy_http_status == 502
    else:
        raise AssertionError("Service failures must not be accepted or misclassified as auth")
    print("Installed Core: platform imports, DeviceInfo, coordinator and proxy error contracts OK")


if __name__ == "__main__":
    main()
