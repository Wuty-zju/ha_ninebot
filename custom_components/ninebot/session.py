"""Private candidate sessions with recoverable, same-filesystem commits."""

import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from .adapters import profiles, text
from .client import NinecliClient
from .exceptions import ErrorKind, NinebotError

type ClientFactory = Callable[[Path, aiohttp.ClientSession], NinecliClient]


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise NinebotError(ErrorKind.PROTOCOL)
    path.chmod(0o700)


def session_uid(path: Path) -> str:
    """Only a completed business login identifies the account."""
    file = path / "tokens.json"
    if file.is_symlink():
        raise NinebotError(ErrorKind.PROTOCOL)
    try:
        raw = json.loads(file.read_bytes())
    except (OSError, ValueError) as err:
        raise NinebotError(ErrorKind.PROTOCOL) from err
    uid = text(raw.get("business_uid")) if isinstance(raw, dict) else None
    if uid is None:
        raise NinebotError(ErrorKind.PROTOCOL)
    return uid


def secure_files(path: Path) -> None:
    for file in path.iterdir():
        if file.is_symlink() or not file.is_file():
            raise NinebotError(ErrorKind.PROTOCOL)
        file.chmod(0o600)


@dataclass(frozen=True)
class Candidate:
    path: Path
    uid: str


class SessionManager:
    """Candidates are never allowed to replace sessions during validation."""

    def __init__(
        self,
        root: Path,
        session: aiohttp.ClientSession,
        client_factory: ClientFactory = NinecliClient,
    ) -> None:
        self.root = root
        self._session = session
        self._factory = client_factory
        self._lock = asyncio.Lock()

    def path(self, key: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", key) is None:
            raise NinebotError(ErrorKind.PROTOCOL)
        return self.root / key

    def _candidate_dir(self) -> Path:
        private_directory(self.root)
        return Path(tempfile.mkdtemp(prefix=".candidate-", dir=self.root))

    async def async_prepare(self, account: str, password: str) -> Candidate:
        task = asyncio.create_task(asyncio.to_thread(self._candidate_dir))
        try:
            directory = await asyncio.shield(task)
        except asyncio.CancelledError:
            directory = await task
            await asyncio.to_thread(shutil.rmtree, directory, True)
            raise
        client = self._factory(directory, self._session)
        try:
            await client.async_login(account, password)
            profiles(await client.async_list_vehicles())
            uid = await asyncio.to_thread(session_uid, directory)
            await client.async_close()
            await asyncio.to_thread(secure_files, directory)
            return Candidate(directory, uid)
        except BaseException:
            await client.async_close()
            await asyncio.to_thread(shutil.rmtree, directory, True)
            raise

    async def async_discard(self, candidate: Candidate) -> None:
        await asyncio.to_thread(shutil.rmtree, candidate.path, True)

    def _recover(self, key: str) -> None:
        destination = self.path(key)
        backup = self.root / f".backup-{key}"
        if (self.root / f".transaction-{key}.json").exists():
            self._rollback(key)
            return
        # Also recover a pre-journal backup (or a manual recovery fixture).
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        elif backup.exists():
            shutil.rmtree(backup)
        (self.root / f".journal-{key}.tmp").unlink(missing_ok=True)

    async def async_recover(self, key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._recover, key)

    def _commit(self, candidate: Candidate, key: str) -> None:
        destination = self.path(key)
        if candidate.path.parent != self.root or not candidate.path.name.startswith(".candidate-"):
            raise NinebotError(ErrorKind.PROTOCOL)
        if session_uid(candidate.path) != candidate.uid:
            raise NinebotError(ErrorKind.PROTOCOL)
        self._recover(key)
        backup = self.root / f".backup-{key}"
        if backup.exists():
            raise NinebotError(ErrorKind.BUSY)
        journal = self.root / f".transaction-{key}.json"
        temporary_journal = self.root / f".journal-{key}.tmp"
        with temporary_journal.open("x", encoding="utf-8") as file:
            file.write(
                json.dumps({"had_previous": destination.exists(), "candidate": candidate.path.name})
            )
            file.flush()
            os.fsync(file.fileno())
        temporary_journal.chmod(0o600)
        os.replace(temporary_journal, journal)
        try:
            if destination.exists():
                os.replace(destination, backup)
            os.replace(candidate.path, destination)
        except OSError:
            self._rollback(key)
            raise

    async def async_commit(self, candidate: Candidate, key: str) -> None:
        """Caller checks uniqueness and closes the old runtime before commit."""
        async with self._lock:
            # Once started, the filesystem transaction must finish even when
            # the flow is cancelled; then roll it back before propagating.
            task = asyncio.create_task(asyncio.to_thread(self._commit, candidate, key))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                await asyncio.to_thread(self._rollback, key)
                raise

    def _rollback(self, key: str) -> None:
        destination = self.path(key)
        backup = self.root / f".backup-{key}"
        journal = self.root / f".transaction-{key}.json"
        if not journal.exists():
            return
        record = json.loads(journal.read_bytes())
        if not isinstance(record, dict) or type(record.get("had_previous")) is not bool:
            raise NinebotError(ErrorKind.PROTOCOL)
        if backup.exists():
            shutil.rmtree(destination, ignore_errors=True)
            os.replace(backup, destination)
        elif not record["had_previous"]:
            shutil.rmtree(destination, ignore_errors=True)
        candidate = record.get("candidate")
        if isinstance(candidate, str) and re.fullmatch(r"\.candidate-[\w-]+", candidate):
            shutil.rmtree(self.root / candidate, ignore_errors=True)
        journal.unlink()

    async def async_rollback(self, key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._rollback, key)

    async def async_finalize(self, key: str) -> None:
        async with self._lock:

            def finalize() -> None:
                (self.root / f".transaction-{key}.json").unlink(missing_ok=True)
                shutil.rmtree(self.root / f".backup-{key}", ignore_errors=True)

            task = asyncio.create_task(asyncio.to_thread(finalize))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def async_import(self, source: Path, expected_uid: str) -> tuple[str, Candidate]:
        """Copy known CLI files only; never mutate a v1 session."""

        def copy() -> Candidate:
            if source.is_symlink() or session_uid(source) != expected_uid:
                raise NinebotError(ErrorKind.PROTOCOL)
            directory = self._candidate_dir()
            try:
                for name in ("config.json", "tokens.json", "vehicles.json"):
                    file = source / name
                    if file.is_symlink():
                        raise NinebotError(ErrorKind.PROTOCOL)
                    if file.is_file():
                        shutil.copyfile(file, directory / name)
                secure_files(directory)
                return Candidate(directory, expected_uid)
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise

        task = asyncio.create_task(asyncio.to_thread(copy))
        try:
            candidate = await asyncio.shield(task)
        except asyncio.CancelledError:
            candidate = await task
            await self.async_discard(candidate)
            raise
        return uuid.uuid4().hex, candidate
