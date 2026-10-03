"""Ninebot App integration with typed runtime and conservative migration."""

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import NinecliClient
from .const import CONF_BUSINESS_UID, CONF_SESSION_KEY, PLATFORMS, SESSION_DIRECTORY
from .coordinator import NinebotCoordinator
from .exceptions import NinebotError
from .migration import async_migrate
from .runtime import NinebotConfigEntry, RuntimeData
from .session import SessionManager, session_uid
from .storage import ModelStorage


def manager_for(hass: HomeAssistant) -> SessionManager:
    return SessionManager(
        Path(hass.config.path(".storage", SESSION_DIRECTORY)), async_get_clientsession(hass)
    )


async def async_setup_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> bool:
    manager = manager_for(hass)
    key = entry.data[CONF_SESSION_KEY]
    await manager.async_recover(key)
    try:
        uid = await hass.async_add_executor_job(session_uid, manager.path(key))
    except NinebotError as err:
        raise ConfigEntryAuthFailed("auth") from err
    if uid != entry.data.get(CONF_BUSINESS_UID):
        raise ConfigEntryAuthFailed("auth")
    client = NinecliClient(manager.path(key), async_get_clientsession(hass))
    store = ModelStorage(hass, entry.entry_id)
    coordinator = NinebotCoordinator(hass, entry, client, models=store)
    entry.runtime_data = RuntimeData(client, coordinator, manager, store)
    try:
        await store.async_load()
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_close()
        raise
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.async_close()
        await entry.runtime_data.models.async_save()
        return True
    return False


async def async_update_options(hass: HomeAssistant, entry: NinebotConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> bool:
    return await async_migrate(hass, entry, manager_for(hass))
