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
from homeassistant.helpers import issue_registry as ir

from .archive_transfer import readonly, snapshot
from .backup import ACTORS_KEY, backup_active, register_archive, unregister_archive
from .const import DOMAIN
from .ride_archive import ArchiveError, ArchiveFailure, RideArchive

CONF_RETAIN_ARCHIVE = "retain_history_database"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DB = re.compile(
    r"rides(?:\.schema[1-7]-[0-9a-f]{64}|\.recovery-[0-9a-f]+)?\.sqlite3(?:-(?:journal|wal|shm))?\Z"
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
    """Only managed names/locations; never scan session files or another account."""
    root = Path(hass.config.path(".storage", DOMAIN))
    canonical = account_path(hass, uid)
    candidates: list[Path] = []

    def files(folder: Path, *, imports: bool = False) -> None:
        if folder.is_symlink() or not folder.is_dir():
            return
        for path in folder.iterdir():
            known = (
                bool(re.fullmatch(r"[0-9a-f]{64}\.sqlite3", path.name))
                if imports
                else bool(_DB.fullmatch(path.name)) or path.name == "recovery.sqlite3"
            )
            if (
                known
                and path.suffix == ".sqlite3"
                and path != canonical
                and not path.is_symlink()
                and path.is_file()
            ):
                candidates.append(path)

    archives = root / "archives"
    if archives.is_dir() and not archives.is_symlink():
        for folder in archives.iterdir():
            if _HEX.fullmatch(folder.name):
                files(folder)
    if canonical.parent.is_symlink():
        raise ArchiveError(ArchiveFailure.STORAGE)
    files(canonical.parent)
    files(canonical.parent / "imports", imports=True)
    retired = canonical.parent / "retired"
    if retired.is_dir() and not retired.is_symlink():
        for folder in retired.iterdir():
            if _HEX.fullmatch(folder.name):
                files(folder)
    result: list[tuple[int, int, Path]] = []
    for path in candidates:
        try:
            db = readonly(path)
        except (OSError, ValueError, sqlite3.Error):
            if path.is_relative_to(canonical.parent):
                result.append((0, 0, path))  # Report, but never remove an unreadable own source.
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
        except sqlite3.Error:
            if path.is_relative_to(canonical.parent):
                result.append((0, 0, path))
        finally:
            db.close()
    return [p for _, _, p in sorted(result, key=lambda item: (-item[0], -item[1], str(item[2])))]


def _copy_source(path: Path, target: Path) -> None:
    db = readonly(path)
    try:
        snapshot(db, target)
    finally:
        db.close()


def _identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or any(
        Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
    ):
        raise ArchiveError(ArchiveFailure.BUSY)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _discard(source: Path, expected: tuple[int, int, int, int], boundary: Path) -> None:
    """Only a checked, unchanged, inactive source after the destination committed."""
    if _identity(source) != expected or source.resolve() != source.absolute():
        raise ArchiveError(ArchiveFailure.BUSY)
    descriptor = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.stat(source.name, dir_fd=descriptor, follow_symlinks=False)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected:
            raise ArchiveError(ArchiveFailure.BUSY)
        os.unlink(source.name, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    folder = source.parent
    while folder != boundary:
        try:
            folder.rmdir()  # Never remove a nonempty directory or an unknown file.
        except OSError:
            break
        folder = folder.parent


async def async_prepare_account(hass: HomeAssistant, uid: str, max_bytes: int) -> None:
    """Consolidate legacy business evidence, then remove only committed sources."""
    async with account_lock(hass, uid):
        if backup_active(hass):
            raise ArchiveError(ArchiveFailure.BUSY)
        target = account_path(hass, uid)
        sources = await hass.async_add_executor_job(_sources, hass, uid)
        if not sources:

            def old_schema() -> bool:
                if not target.exists():
                    return False
                from .ride_archive import SCHEMA_VERSION

                db = readonly(target)
                try:
                    return db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                finally:
                    db.close()

            if not await hass.async_add_executor_job(old_schema):
                return
        actor = RideArchive(target, owner_key(uid), max_bytes=max_bytes)
        register_archive(hass, actor)
        try:
            await actor.async_open()
            # Opening an older canonical DB creates its schema safety copy.
            sources = await hass.async_add_executor_job(_sources, hass, uid)
            failed = False
            for source in sources:
                if any(
                    other is not actor and other.path == source
                    for other in hass.data.get(ACTORS_KEY, {}).values()
                ):
                    failed = True
                    continue
                # Migrate a private copy, never rewrite an old evidence original.
                directory = await hass.async_add_executor_job(tempfile.mkdtemp)
                copy = Path(directory) / "rides.sqlite3"
                candidate = RideArchive(copy, owner_key(uid), max_bytes=max_bytes)
                try:
                    identity = await hass.async_add_executor_job(_identity, source)
                    await hass.async_add_executor_job(_copy_source, source, copy)
                    await candidate.async_open()
                    await candidate.async_close()
                    await actor.async_merge_archive(copy)

                    def discard(
                        source: Path = source, identity: tuple[int, int, int, int] = identity
                    ) -> None:
                        actor._db(write=True)  # Share the official backup barrier.
                        _discard(source, identity, target.parent.parent.parent)

                    await actor._run(discard)
                except (ArchiveError, OSError, ValueError, sqlite3.Error):
                    failed = True  # Keep the source; one failure cannot hide healthy history.
                finally:
                    await candidate.async_close()
                    await hass.async_add_executor_job(shutil.rmtree, directory)
            issue = f"archive_cleanup_{owner_key(uid)}"
            if failed:
                ir.async_create_issue(
                    hass,
                    DOMAIN,
                    issue,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="archive_cleanup",
                )
            else:
                ir.async_delete_issue(hass, DOMAIN, issue)
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


async def async_remove_account(hass: HomeAssistant, uid: str, entry_id: str | None = None) -> None:
    async with account_lock(hass, uid):
        # A backup can defer deletion past removal/re-addition of the old entry.
        # Never erase a database already reclaimed by another authenticated entry.
        if entry_id is not None and any(
            entry.entry_id != entry_id and entry.data.get("business_uid") == uid
            for entry in hass.config_entries.async_entries(DOMAIN)
        ):
            raise ArchiveError(ArchiveFailure.BUSY)
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
