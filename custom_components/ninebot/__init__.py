"""The Ninebot integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .api import NinebotApiClient
from .const import (
    BINARY_SENSOR_KEYS,
    CONF_DEBUG,
    CONF_LANG,
    DATA_COORDINATOR,
    DEFAULT_DEBUG,
    DEFAULT_LANG,
    DOMAIN,
    IMAGE_KEYS,
    LOCK_KEYS,
    PLATFORMS,
    SENSOR_KEYS,
)
from .coordinator import NinebotDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _expected_entity_id(platform: str, sn: str, key: str) -> str:
    return f"{platform}.ninebot_{sn}_{key}".lower()


def _expected_unique_id(sn: str, key: str) -> str:
    return f"ninebot_{sn}_{key}".lower()


def _build_expected_registry_map(data: dict[str, dict]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for sn in data:
        for key in SENSOR_KEYS:
            expected[_expected_unique_id(sn, key)] = _expected_entity_id("sensor", sn, key)
        for key in BINARY_SENSOR_KEYS:
            expected[_expected_unique_id(sn, key)] = _expected_entity_id("binary_sensor", sn, key)
        for key in IMAGE_KEYS:
            expected[_expected_unique_id(sn, key)] = _expected_entity_id("image", sn, key)
        for key in LOCK_KEYS:
            expected[_expected_unique_id(sn, key)] = _expected_entity_id("lock", sn, key)
    return expected


def _is_ninebot_entity(entry: er.RegistryEntry) -> bool:
    return entry.platform == DOMAIN and entry.domain in {"sensor", "binary_sensor", "image", "lock"}


async def _async_enforce_entity_registry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: NinebotDataUpdateCoordinator,
) -> None:
    """Force registry entries to strict deterministic naming rules.

    Removes historical slugs/suffixed IDs and keeps only current required entities.
    """
    entity_registry = er.async_get(hass)
    existing = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    expected_by_unique = _build_expected_registry_map(coordinator.data)

    existing_by_entity_id = {entry.entity_id: entry for entry in existing}

    for entry in existing:
        if not _is_ninebot_entity(entry):
            continue

        expected_entity_id = expected_by_unique.get(entry.unique_id)
        if expected_entity_id is None:
            entity_registry.async_remove(entry.entity_id)
            continue

        if entry.entity_id == expected_entity_id:
            continue

        conflict = existing_by_entity_id.get(expected_entity_id)
        if conflict and conflict.entity_id != entry.entity_id:
            entity_registry.async_remove(conflict.entity_id)

        entity_registry.async_update_entity(entry.entity_id, new_entity_id=expected_entity_id)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration from yaml (not used)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ninebot from a config entry."""
    session = async_get_clientsession(hass)
    client = NinebotApiClient(
        session=session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        lang=entry.data.get(CONF_LANG, DEFAULT_LANG),
        debug=entry.data.get(CONF_DEBUG, DEFAULT_DEBUG),
    )
    coordinator = NinebotDataUpdateCoordinator(
        hass,
        config_entry=entry,
        api_client=client,
    )

    await coordinator.async_config_entry_first_refresh()

    await _async_enforce_entity_registry(hass, entry, coordinator)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {DATA_COORDINATOR: coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("Ninebot entry %s setup completed", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
