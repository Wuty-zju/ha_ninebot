"""Official HA backup hooks: quiesce our DELETE-journal archives, no cloud I/O."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .ride_archive import ArchiveError, RideArchive

BACKUP_KEY = f"{DOMAIN}_archive_backup"
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


async def async_pre_backup(hass: HomeAssistant) -> None:
    if backup_active(hass):
        raise HomeAssistantError("Ninebot archive backup is already in progress")
    state = BackupState(list(hass.data.get(ACTORS_KEY, {}).values()))
    hass.data[BACKUP_KEY] = state  # Setup cannot create another writer mid-backup.
    try:
        for archive in state.archives:
            await archive.async_prepare_backup()
    except BaseException as err:
        # Cancellation or one failed actor cannot leave the other accounts frozen.
        hass.data.pop(BACKUP_KEY, None)
        for archive in state.archives:
            archive.resume_after_backup()
        for remove in state.removals:
            await remove()
        if isinstance(err, ArchiveError):
            raise HomeAssistantError("Ninebot archive could not be prepared for backup") from None
        raise


async def async_post_backup(hass: HomeAssistant) -> None:
    state: BackupState | None = hass.data.pop(BACKUP_KEY, None)
    if state is None:
        return
    for archive in state.archives:
        archive.resume_after_backup()
    for remove in state.removals:
        await remove()
