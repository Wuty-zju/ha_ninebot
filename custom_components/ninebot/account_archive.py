"""Account-owned archive location, retained-entry adoption and safe deletion."""

import asyncio
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path

from homeassistant.core import HomeAssistant

from .archive_transfer import readonly, snapshot
from .backup import backup_active, register_archive, unregister_archive
from .const import DOMAIN
from .ride_archive import ArchiveError, ArchiveFailure, RideArchive

CONF_RETAIN_ARCHIVE = "retain_history_database"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DB = re.compile(
    r"rides(?:\.schema[1-6]-[0-9a-f]{64}|\.recovery-[0-9a-f]+)?\.sqlite3(?:-(?:journal|wal|shm))?\Z"
)


def owner_key(uid: str) -> str:
    return hashlib.sha256(uid.encode()).hexdigest()


def account_path(hass: HomeAssistant, uid: str) -> Path:
    return Path(hass.config.path(".storage", DOMAIN, "accounts", owner_key(uid), "rides.sqlite3"))


def account_lock(hass: HomeAssistant, uid: str) -> asyncio.Lock:
    return hass.data.setdefault(f"{DOMAIN}_account_archive_locks", {}).setdefault(
        owner_key(uid), asyncio.Lock()
    )


def _sources(hass: HomeAssistant, uid: str) -> list[Path]:
    root = Path(hass.config.path(".storage", DOMAIN, "archives"))
    if root.is_symlink() or not root.is_dir():
        return []
    result: list[tuple[int, int, Path]] = []
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir() or not _HEX.fullmatch(folder.name):
            continue
        path = folder / "rides.sqlite3"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            db = readonly(path)
        except (OSError, ValueError, sqlite3.Error):
            continue
        try:
            row = db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
            if row == (owner_key(uid),):
                tables = {
                    r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                verified = (
                    db.execute("SELECT count(*) FROM history_months WHERE verified=1").fetchone()[0]
                    if "history_months" in tables
                    else 0
                )
                rides = db.execute("SELECT count(*) FROM rides").fetchone()[0]
                result.append((verified, rides, path))
        finally:
            db.close()
    return [p for _, _, p in sorted(result, key=lambda item: (-item[0], -item[1], str(item[2])))]


def _copy_source(path: Path, target: Path) -> None:
    db = readonly(path)
    try:
        snapshot(db, target)
    finally:
        db.close()


def _retire(source: Path, destination: Path) -> None:
    """Move only a recognized old directory after its live database was merged."""
    files = list(source.parent.iterdir())
    if any(p.is_symlink() or not p.is_file() or not _DB.fullmatch(p.name) for p in files):
        return  # Leave unusual user files untouched; merge receipt prevents replay.
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not destination.exists():
        os.rename(source.parent, destination)


async def async_prepare_account(hass: HomeAssistant, uid: str, max_bytes: int) -> None:
    """Adopt all verified old entry archives before opening the runtime actor."""
    async with account_lock(hass, uid):
        if backup_active(hass):
            raise ArchiveError(ArchiveFailure.BUSY)
        sources = await hass.async_add_executor_job(_sources, hass, uid)
        if not sources:
            return
        target = account_path(hass, uid)
        actor = RideArchive(target, owner_key(uid), max_bytes=max_bytes)
        register_archive(hass, actor)
        try:
            await actor.async_open()
            for source in sources:
                # Migrate a private copy, never rewrite an old evidence original.
                directory = await hass.async_add_executor_job(tempfile.mkdtemp)
                copy = Path(directory) / "rides.sqlite3"
                candidate = RideArchive(copy, owner_key(uid), max_bytes=max_bytes)
                try:
                    await hass.async_add_executor_job(_copy_source, source, copy)
                    await candidate.async_open()
                    await candidate.async_close()
                    await actor.async_merge_archive(copy)

                    def retire(source: Path = source) -> None:
                        actor._db(write=True)  # Share the official backup barrier.
                        _retire(source, target.parent / "retired" / source.parent.name)

                    await actor._run(retire)
                finally:
                    await candidate.async_close()
                    await hass.async_add_executor_job(shutil.rmtree, directory)
        finally:
            await actor.async_close()
            unregister_archive(hass, actor)


def _safe_remove(root: Path, owner: str, *, check_only: bool = False) -> None:
    """Preflight a bounded owned tree using no-follow directory descriptors."""
    descriptors: list[int] = []
    files: list[tuple[int, str]] = []
    directories: list[tuple[int, str]] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def walk(directory: int, rel: tuple[str, ...]) -> None:
        if len(descriptors) > 128 or len(files) > 4096:
            raise ArchiveError(ArchiveFailure.STORAGE)
        for name in os.listdir(directory):
            mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                permitted = (not rel and name in {"imports", "retired"}) or (
                    rel == ("retired",) and _HEX.fullmatch(name)
                )
                if not permitted:
                    raise ArchiveError(ArchiveFailure.STORAGE)
                child = os.open(name, flags, dir_fd=directory)
                descriptors.append(child)
                walk(child, (*rel, name))
                directories.append((directory, name))
                continue
            if rel == ("imports",):
                known = name == "pending.sqlite3" or bool(
                    re.fullmatch(r"[0-9a-f]{64}\.sqlite3", name)
                )
            elif not rel:
                known = bool(_DB.fullmatch(name)) or name in {
                    "recovery.sqlite3",
                    "recovery.pending.sqlite3",
                }
            else:
                known = bool(_DB.fullmatch(name))
            if not stat.S_ISREG(mode) or not known:
                raise ArchiveError(ArchiveFailure.STORAGE)
            files.append((directory, name))

    try:
        for ancestor in (root.parent.parent, root.parent, root):
            if ancestor.is_symlink():
                raise ArchiveError(ArchiveFailure.STORAGE)
        try:
            parent = os.open(root, flags)
        except FileNotFoundError:
            return
        descriptors.append(parent)
        try:
            directory = os.open(owner, flags, dir_fd=parent)
        except FileNotFoundError:
            return
        descriptors.append(directory)
        walk(directory, ())
        if check_only:
            return
        for descriptor, name in files:
            os.unlink(name, dir_fd=descriptor)
        for descriptor, name in directories:
            os.rmdir(name, dir_fd=descriptor)
        os.rmdir(owner, dir_fd=parent)
    except OSError:
        raise ArchiveError(ArchiveFailure.STORAGE) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


async def async_remove_account(hass: HomeAssistant, uid: str) -> None:
    async with account_lock(hass, uid):
        sources = await hass.async_add_executor_job(_sources, hass, uid)
        targets = [(account_path(hass, uid).parent.parent, owner_key(uid))]
        targets.extend((p.parent.parent, p.parent.name) for p in sources)

        def remove() -> None:
            # Check all known same-owner locations before unlinking any file.
            for root, scope in targets:
                _safe_remove(root, scope, check_only=True)
            for root, scope in targets:
                _safe_remove(root, scope)

        await hass.async_add_executor_job(remove)
