"""Official HA backup hooks: quiesce our DELETE-journal archives, no cloud I/O."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .ride_archive import ArchiveError, RideArchive

BACKUP_KEY = f"{DOMAIN}_archive_backup"
REMOVALS_KEY = f"{DOMAIN}_archive_pending_removals"
_LOGGER = logging.getLogger(__name__)
ACTORS_KEY = f"{DOMAIN}_archive_actors"


@dataclass
class BackupState:
    archives: list[RideArchive]
    removals: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


def backup_active(hass: HomeAssistant) -> bool:
    return BACKUP_KEY in hass.data


def register_archive(hass: HomeAssistant, archive: RideArchive) -> None:
    """Include setup-in-progress actors and actors created during preparation."""
    hass.data.setdefault(ACTORS_KEY, {})[id(archive)] = archive
    if backup_active(hass):
        state: BackupState = hass.data[BACKUP_KEY]
        archive.backup_paused = True
        state.archives.append(archive)


def unregister_archive(hass: HomeAssistant, archive: RideArchive) -> None:
    hass.data.get(ACTORS_KEY, {}).pop(id(archive), None)


def defer_removal(hass: HomeAssistant, remove: Callable[[], Awaitable[None]]) -> bool:
    """Explicit account deletion waits for the backup's post hook."""
    if not backup_active(hass):
        return False
    state: BackupState = hass.data[BACKUP_KEY]
    state.removals.append(remove)
    return True


async def _finish_backup(hass: HomeAssistant, state: BackupState) -> None:
    """Thaw every account first; drain cleanup despite failure or cancellation."""
    for archive in state.archives:
        archive.resume_after_backup()
    if not state.removals:
        return
    work = asyncio.gather(*(remove() for remove in state.removals), return_exceptions=True)
    cancelled = False
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError:
            cancelled = True
    failed = [
        remove
        for remove, result in zip(state.removals, work.result(), strict=True)
        if isinstance(result, BaseException)
    ]
    if failed:
        hass.data.setdefault(REMOVALS_KEY, []).extend(failed)
        _LOGGER.warning(
            "Deferred archive cleanup failed for %d account(s); retained for retry", len(failed)
        )
    if cancelled or any(isinstance(result, asyncio.CancelledError) for result in work.result()):
        raise asyncio.CancelledError


async def async_pre_backup(hass: HomeAssistant) -> None:
    if backup_active(hass):
        raise HomeAssistantError("Ninebot archive backup is already in progress")
    state = BackupState(
        list(hass.data.get(ACTORS_KEY, {}).values()), hass.data.pop(REMOVALS_KEY, [])
    )
    hass.data[BACKUP_KEY] = state
    try:
        for archive in state.archives:
            await archive.async_prepare_backup()
    except BaseException as err:
        hass.data.pop(BACKUP_KEY, None)
        await _finish_backup(hass, state)
        if isinstance(err, ArchiveError):
            raise HomeAssistantError("Ninebot archive could not be prepared for backup") from None
        raise


async def async_post_backup(hass: HomeAssistant) -> None:
    state: BackupState | None = hass.data.pop(BACKUP_KEY, None)
    if state is None:
        pending: list[Callable[[], Awaitable[None]]] = hass.data.pop(REMOVALS_KEY, [])
        if not pending:
            return
        state = BackupState([], pending)
    await _finish_backup(hass, state)
