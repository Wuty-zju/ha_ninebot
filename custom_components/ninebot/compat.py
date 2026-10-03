"""Centralized public-API capability detection, without HA version strings."""

from homeassistant.helpers import device_registry as dr


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
