"""Account-owned archive location, retained-entry adoption and safe deletion."""

import asyncio
import hashlib
import os
import re
import shutil
import sqlite3
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
    result = []
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
                result.append(path)
        finally:
            db.close()
    return sorted(result)


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
    path = root / owner
    if not path.exists():
        return
    if root.is_symlink() or path.is_symlink():
        raise ArchiveError(ArchiveFailure.STORAGE)
    # Preflight the entire owned tree; unknown files mean no deletion at all.
    allowed: list[Path] = []
    for current, dirs, files in os.walk(path, followlinks=False):
        directory = Path(current)
        rel = directory.relative_to(path).parts
        if rel and not (
            rel == ("retired",)
            or rel == ("imports",)
            or len(rel) == 2
            and rel[0] == "retired"
            and _HEX.fullmatch(rel[1])
        ):
            raise ArchiveError(ArchiveFailure.STORAGE)
        if any((directory / name).is_symlink() for name in (*dirs, *files)):
            raise ArchiveError(ArchiveFailure.STORAGE)
        for name in files:
            known = (
                bool(_DB.fullmatch(name))
                if rel and rel[0] == "retired"
                else name == "pending.sqlite3" or bool(re.fullmatch(r"[0-9a-f]{64}\.sqlite3", name))
                if rel == ("imports",)
                else bool(_DB.fullmatch(name))
                or name
                in {
                    "rides.sqlite3",
                    "rides.sqlite3-journal",
                    "rides.sqlite3-wal",
                    "rides.sqlite3-shm",
                    "recovery.sqlite3",
                    "recovery.pending.sqlite3",
                }
            )
            p = directory / name
            if not known or not p.is_file():
                raise ArchiveError(ArchiveFailure.STORAGE)
            allowed.append(p)
    if check_only:
        return
    for p in allowed:
        p.unlink()
    for current, _, _ in os.walk(path, topdown=False):
        Path(current).rmdir()


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
