"""Ninebot App integration with typed runtime and conservative migration."""

from pathlib import Path

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .archive_lifecycle import async_remove_archive
from .backup import backup_active, defer_removal
from .client import NinecliClient
from .const import CONF_BUSINESS_UID, CONF_SESSION_KEY, DOMAIN, PLATFORMS, SESSION_DIRECTORY
from .coordinator import NinebotCoordinator
from .entity import async_audit_device_identities
from .event_store import RideEventPipeline
from .exceptions import NinebotError
from .identity import IdentityStore
from .migration import async_migrate
from .models import VehicleSnapshot
from .registry import (
    async_enable_configured_controls,
    async_enable_standard_entities,
    async_migrate_entity_ids,
    async_remove_obsolete_entities,
)
from .ride_archive import ArchiveError, ArchiveFailure
from .runtime import NinebotConfigEntry, RuntimeData
from .services import async_register_actions
from .session import SessionManager, session_uid
from .statistics_store import TravelStatisticsStore
from .storage import ModelStorage


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_actions(hass)
    return True


def manager_for(hass: HomeAssistant) -> SessionManager:
    key = f"{DOMAIN}_sessions"
    if key not in hass.data:
        hass.data[key] = SessionManager(
            Path(hass.config.path(".storage", SESSION_DIRECTORY)), async_get_clientsession(hass)
        )
    manager: SessionManager = hass.data[key]
    lifecycle = f"{key}_lifecycle"
    if lifecycle not in hass.data:
        hass.data[lifecycle] = True
        hass.async_create_task(manager.async_cleanup_sms())

        async def cleanup(_: object) -> None:
            await manager.async_cleanup_sms(shutdown=True)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, cleanup)
    return manager


async def async_setup_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> bool:
    if backup_active(hass):
        raise ConfigEntryNotReady("Ninebot archive backup is in progress")
    manager = manager_for(hass)
    key = entry.data[CONF_SESSION_KEY]
    await manager.async_recover(key)
    try:
        uid = await hass.async_add_executor_job(session_uid, manager.path(key))
    except NinebotError as err:
        raise ConfigEntryAuthFailed("auth") from err
    if uid != entry.data.get(CONF_BUSINESS_UID):
        raise ConfigEntryAuthFailed("auth")
    ir.async_delete_issue(hass, DOMAIN, f"session_recovery_{entry.entry_id}")
    client = NinecliClient(manager.path(key), async_get_clientsession(hass))
    store = ModelStorage(hass, entry.entry_id)
    coordinator = NinebotCoordinator(hass, entry, client)
    entry.runtime_data = RuntimeData(
        client,
        coordinator,
        manager,
        store,
        events=RideEventPipeline(hass, entry.entry_id, coordinator),
        identities=IdentityStore(hass, entry),
    )
    try:
        await store.async_load()
        await coordinator.statistics.async_load()
        if coordinator.statistics.archive.backup_paused or (
            not coordinator.statistics.archive_ready
            and coordinator.statistics.error is ArchiveFailure.BUSY
        ):
            raise ConfigEntryNotReady("Ninebot archive backup is in progress")
        assert entry.runtime_data.identities is not None
        await entry.runtime_data.identities.async_load()
        cached = await coordinator.statistics.async_cached_profiles()
        coordinator.data = {profile.sn: VehicleSnapshot(profile) for profile in cached}
        try:
            await coordinator.async_config_entry_first_refresh()
        except (ConfigEntryNotReady, ConfigEntryAuthFailed) as err:
            if not cached:
                raise
            # This is historical availability, never a successful cloud sample.
            if isinstance(err, ConfigEntryAuthFailed):
                entry.async_start_reauth(hass)

        await entry.runtime_data.identities.async_prepare(
            s.profile for s in coordinator.data.values()
        )
        entry.runtime_data.obsolete_entities_removed = async_remove_obsolete_entities(hass, entry)
        entry.runtime_data.standard_entities_enabled = async_enable_standard_entities(hass, entry)
        entry.runtime_data.configured_controls_enabled = async_enable_configured_controls(
            hass, entry
        )
        await async_migrate_entity_ids(hass, entry)
        async_audit_device_identities(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if entry.runtime_data.events:
            await entry.runtime_data.events.async_close()
        await coordinator.async_close()
        raise
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: async_audit_device_identities(hass, entry))
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if entry.runtime_data.events:
            await entry.runtime_data.events.async_close()
        await entry.runtime_data.coordinator.async_close()
        await entry.runtime_data.models.async_save()
        return True
    return False


async def async_update_options(hass: HomeAssistant, entry: NinebotConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> bool:
    return await async_migrate(hass, entry, manager_for(hass))


async def async_remove_entry(hass: HomeAssistant, entry: NinebotConfigEntry) -> None:
    if isinstance(runtime := getattr(entry, "runtime_data", None), RuntimeData):
        # HA still calls remove when platform unload failed. No SQL actor may
        # remain open over a file that is about to be removed.
        if runtime.events:
            await runtime.events.async_close()
        await runtime.coordinator.async_close()

    async def remove() -> None:
        try:
            await async_remove_archive(hass, entry.entry_id)
        except ArchiveError:
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"ride_archive_remove_{entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="ride_archive_removal",
            )
            return
        ir.async_delete_issue(hass, DOMAIN, f"ride_archive_remove_{entry.entry_id}")
        ir.async_delete_issue(hass, DOMAIN, f"ride_archive_{entry.entry_id}")

    if not defer_removal(hass, remove):
        await remove()
    await TravelStatisticsStore(hass, entry.entry_id).async_remove()
    await IdentityStore(hass, entry).async_remove()
