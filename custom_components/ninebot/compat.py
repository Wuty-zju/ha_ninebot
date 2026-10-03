"""Centralized public-API capability detection, without HA version strings."""


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
