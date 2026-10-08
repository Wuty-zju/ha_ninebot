"""Centralized public-API capability detection, without HA version strings."""

from importlib import import_module
from types import ModuleType

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity

# HA's documented startup alias resolves this public name to Probatio on new
# Core, and to voluptuous on old Core. Dynamic resolution is confined here:
# static module types disagree about Schema even though the runtime API matches.
# Do not choose merely because probatio happens to be installed on an old HA.
validation: ModuleType = import_module("voluptuous")


def update_calendar_listeners(entity: Entity) -> None:
    """New Core subscriptions; older Core still supports ordinary range reads."""
    if callable(notify := getattr(entity, "async_update_event_listeners", None)):
        notify()


def unrecorded_attributes(attributes: frozenset[str]) -> frozenset[str]:
    """Progressively exclude debug views from Recorder; views remain bounded."""
    return attributes if hasattr(Entity, "_unrecorded_attributes") else frozenset()


def child_registry_api_available() -> bool:
    """Report registry APIs, not eligibility of a battery to become a child.

    Physical identity and composition still need evidence. Do not register a
    child just because this runtime can do so.
    """
    return (
        hasattr(dr, "ChildDeviceInfo")
        and callable(getattr(dr.DeviceRegistry, "async_get_or_create_child", None))
        and callable(getattr(dr.DeviceRegistry, "async_get_child_device_by_identifier", None))
    )


def device_entry_ids(device: object) -> frozenset[str]:
    """New registry entries have one owner; old entries may have several.

    A composite without a concrete owner is deliberately not routed by guessing
    its primary entry. Callers must select an unambiguous vehicle device.
    """
    if hasattr(device, "config_entry_id"):
        owner = device.config_entry_id
        return frozenset({owner}) if isinstance(owner, str) and owner else frozenset()
    owners = getattr(device, "config_entries", ())
    if isinstance(owners, (set, frozenset)):
        return frozenset(owner for owner in owners if isinstance(owner, str))
    return frozenset()


def is_child_device(device: object) -> bool:
    """Child components cannot be used as cloud vehicle identifiers."""
    return getattr(device, "parent_device_id", None) is not None


def device_by_identifier(
    registry: dr.DeviceRegistry, entry_id: str, identifier: str
) -> object | None:
    """New HA matches identifiers inside the owner; old HA has a global index."""
    from inspect import signature
    from typing import Any, Callable, cast

    lookup = cast(Callable[..., Any], registry.async_get_device)
    if "config_entry_id" in signature(lookup).parameters:
        return lookup(identifiers={("ninebot", identifier)}, config_entry_id=entry_id)
    return lookup(identifiers={("ninebot", identifier)})
