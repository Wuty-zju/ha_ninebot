"""Private candidate sessions with recoverable, same-filesystem commits."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from .account import AccountDisplay
from .adapters import profiles, text
from .client import NinecliClient
from .exceptions import ErrorKind, NinebotError

type ClientFactory = Callable[[Path, aiohttp.ClientSession], NinecliClient]


async def finish_io[T](operation: Callable[[], T]) -> T:
    """A cancelled coroutine must not leave an executor write racing cleanup."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise NinebotError(ErrorKind.PROTOCOL)
    path.chmod(0o700)


def session_uid(path: Path) -> str:
    """Only a completed business login identifies the account."""
    file = path / "tokens.json"
    if path.is_symlink() or file.is_symlink():
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
    display: AccountDisplay = field(default_factory=AccountDisplay, repr=False)


SMS_COOLDOWN = 60
SMS_LIFETIME = 600


@dataclass
class SmsChallenge:
    path: Path
    account: str = field(repr=False)
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    consumed: bool = False


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
        self._transactions: dict[str, asyncio.Lock] = {}
        self._live_transactions: set[str] = set()
        self._sms: dict[Path, SmsChallenge] = {}
        self._sms_attempts: dict[str, float] = {}

    async def async_cleanup_sms(self, *, shutdown: bool = False) -> None:
        """Collect expired live challenges and old SMS-only crash remnants."""
        for challenge in list(self._sms.values()):
            if shutdown or time.monotonic() >= challenge.expires_at:
                await self.async_discard_sms(challenge)

        def collect() -> None:
            if not self.root.is_dir() or self.root.is_symlink():
                return
            for directory in self.root.glob(".candidate-sms-*"):
                if directory in self._sms or directory.is_symlink() or not directory.is_dir():
                    continue
                if time.time() - directory.stat().st_mtime >= SMS_LIFETIME:
                    shutil.rmtree(directory, ignore_errors=True)

        await finish_io(collect)

    async def async_send_sms(
        self, account: str, challenge: SmsChallenge | None = None
    ) -> SmsChallenge:
        """Exactly one explicit send. Cooldown covers unknown/failed delivery too."""
        identity = hashlib.sha256(account.encode()).hexdigest()
        stamp = time.monotonic()
        if stamp - self._sms_attempts.get(identity, -SMS_COOLDOWN) < SMS_COOLDOWN:
            raise NinebotError(ErrorKind.BUSY)
        self._sms_attempts = {
            key: value for key, value in self._sms_attempts.items() if stamp - value < SMS_COOLDOWN
        }
        self._sms_attempts[identity] = stamp
        await self.async_cleanup_sms()
        created = challenge is None
        if challenge is None:
            if len(self._sms) >= 8:
                raise NinebotError(ErrorKind.BUSY)

            def create() -> Path:
                private_directory(self.root)
                return Path(tempfile.mkdtemp(prefix=".candidate-sms-", dir=self.root))

            task = asyncio.create_task(asyncio.to_thread(create))
            try:
                directory = await asyncio.shield(task)
            except asyncio.CancelledError:
                directory = await task
                await finish_io(lambda: shutil.rmtree(directory, True))
                raise
            challenge = SmsChallenge(directory, account, stamp + SMS_LIFETIME)
            self._sms[directory] = challenge
        if (
            self._sms.get(challenge.path) is not challenge
            or challenge.account != account
            or challenge.consumed
        ):
            raise NinebotError(ErrorKind.PROTOCOL)
        try:
            async with challenge.lock:
                client = self._factory(challenge.path, self._session)
                try:
                    await client.async_send_login_code(account)
                    await finish_io(lambda: secure_files(challenge.path))
                    challenge.expires_at = time.monotonic() + SMS_LIFETIME
                finally:
                    await client.async_close()
        except BaseException:
            # A failed first send never leaves an ownerless candidate. Keep a
            # prior challenge on failed explicit resend so the user can retry.
            if created:
                await self.async_discard_sms(challenge)
            raise
        return challenge

    async def async_consume_sms(self, challenge: SmsChallenge, code: str) -> Candidate:
        """Validate in the same private directory, never in the active session."""
        if re.fullmatch(r"[0-9]{4,8}", code) is None:
            raise NinebotError(ErrorKind.PROTOCOL)
        async with challenge.lock:
            if (
                self._sms.get(challenge.path) is not challenge
                or challenge.consumed
                or time.monotonic() >= challenge.expires_at
            ):
                raise NinebotError(ErrorKind.AUTH)
            client = self._factory(challenge.path, self._session)
            try:
                await client.async_consume_login_code(challenge.account, code)
                display = await self._account_display(client)
                profiles(await client.async_list_vehicles())
                uid = await finish_io(lambda: session_uid(challenge.path))
                await client.async_close()
                await finish_io(lambda: secure_files(challenge.path))
                challenge.consumed = True
                self._sms.pop(challenge.path, None)
                return Candidate(challenge.path, uid, display)
            finally:
                await client.async_close()

    async def async_discard_sms(self, challenge: SmsChallenge) -> None:
        async with challenge.lock:
            self._sms.pop(challenge.path, None)
            if not challenge.consumed:
                await finish_io(lambda: shutil.rmtree(challenge.path, True))

    @asynccontextmanager
    async def transaction(self, key: str) -> AsyncIterator[None]:
        """Serialize the entire entry change, including its runtime reload.

        Setup during this reload must not mistake our pending journal for a
        crashed flow. A fresh HA instance has no live transactions and recovers
        that journal normally.
        """
        self.path(key)
        lock = self._transactions.setdefault(key, asyncio.Lock())
        async with lock:
            self._live_transactions.add(key)
            try:
                yield
            finally:
                self._live_transactions.discard(key)

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
        client = None
        try:
            client = self._factory(directory, self._session)
            await client.async_login(account, password)
            display = await self._account_display(client)
            profiles(await client.async_list_vehicles())
            uid = await finish_io(lambda: session_uid(directory))
            await client.async_close()
            await finish_io(lambda: secure_files(directory))
            return Candidate(directory, uid, display)
        except BaseException:
            try:
                if client is not None:
                    await client.async_close()
            finally:
                await finish_io(lambda: shutil.rmtree(directory, True))
            raise

    async def async_discard(self, candidate: Candidate) -> None:
        await finish_io(lambda: shutil.rmtree(candidate.path, True))

    @staticmethod
    async def _account_display(client: NinecliClient) -> AccountDisplay:
        try:
            return AccountDisplay.parse(await client.async_get_account())
        except NinebotError as err:
            if err.kind == ErrorKind.AUTH:
                raise
            # Optional display data cannot invalidate otherwise verified login.
            return AccountDisplay()

    def _recover(self, key: str) -> None:
        destination = self.path(key)
        backup = self.root / f".backup-{key}"
        journal = self.root / f".transaction-{key}.json"
        if any(path.is_symlink() for path in (destination, backup, journal)):
            raise NinebotError(ErrorKind.PROTOCOL)
        if journal.exists():
            self._rollback(key)
            return
        # Also recover a pre-journal backup (or a manual recovery fixture).
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        elif backup.exists():
            shutil.rmtree(backup)
        (self.root / f".journal-{key}.tmp").unlink(missing_ok=True)

    async def async_recover(self, key: str) -> None:
        if key in self._live_transactions:
            return
        async with self._lock:
            await finish_io(lambda: self._recover(key))

    def _commit(self, candidate: Candidate, key: str) -> None:
        destination = self.path(key)
        if (
            candidate.path.parent != self.root
            or re.fullmatch(r"\.candidate-[\w-]+", candidate.path.name) is None
        ):
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

    def _rollback(self, key: str) -> bool:
        destination = self.path(key)
        backup = self.root / f".backup-{key}"
        journal = self.root / f".transaction-{key}.json"
        if not journal.exists():
            return False
        if any(path.is_symlink() for path in (destination, backup, journal)):
            raise NinebotError(ErrorKind.PROTOCOL)
        try:
            record = json.loads(journal.read_bytes())
        except (OSError, ValueError) as err:
            raise NinebotError(ErrorKind.PROTOCOL) from err
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
        return True

    async def async_rollback(self, key: str) -> bool:
        async with self._lock:
            return await finish_io(lambda: self._rollback(key))

    async def async_is_pending(self, key: str) -> bool:
        self.path(key)
        return await finish_io(lambda: (self.root / f".transaction-{key}.json").exists())

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
