"""Delete only an explicitly removed entry's private files, without recursion."""

import hashlib
import os
import stat
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .ride_archive import ArchiveError, ArchiveFailure

ARCHIVE_FILES = frozenset(
    {"rides.sqlite3", "rides.sqlite3-journal", "rides.sqlite3-wal", "rides.sqlite3-shm"}
)


def _remove(root: Path, entry_id: str) -> None:
    """Walk owned directories with no-follow FDs; leave unexpected files intact."""
    scope = hashlib.sha256(entry_id.encode()).hexdigest()
    descriptors: list[int] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptors.append(os.open(root, flags))
        for name in (".storage", DOMAIN, "archives", scope):
            try:
                descriptors.append(os.open(name, flags, dir_fd=descriptors[-1]))
            except FileNotFoundError:
                return
        directory = descriptors[-1]
        names = os.listdir(directory)
        if set(names) - ARCHIVE_FILES or any(
            not stat.S_ISREG(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)
            for name in names
        ):
            raise ArchiveError(ArchiveFailure.STORAGE)
        for name in names:
            os.unlink(name, dir_fd=directory)
        os.rmdir(scope, dir_fd=descriptors[-2])
    except OSError:
        raise ArchiveError(ArchiveFailure.STORAGE) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


async def async_remove_archive(hass: HomeAssistant, entry_id: str) -> None:
    await hass.async_add_executor_job(_remove, Path(hass.config.config_dir), entry_id)
