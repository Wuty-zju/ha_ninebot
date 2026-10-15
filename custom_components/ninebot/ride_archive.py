"""Account-scoped SQLite ride archive, serialized entirely outside HA's loop.

Reviewed scalar facts and bounded private detail responses persist. The bounded
RawStore remains the live response cache; archived GPS is never HA state.
Historical membership is retained even
when a subsequent incomplete month response omits previously observed rides.
"""

import asyncio
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as WorkerTimeout
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar
from zoneinfo import ZoneInfo

from .archive_audit import AuditPlan
from .archive_browse import RecordedCursor, RecordedCursorMismatch, RecordedPage, recorded_page
from .archive_codec import (
    decoded,
    encoded,
    merged_ride,
    month_data,
    profile_data,
    restored_month,
    restored_profile,
    restored_ride,
    ride_data,
    stamp,
)
from .archive_legacy import prepare_legacy
from .archive_timeline import (
    MAX_TIMELINE_BYTES,
    MAX_TIMELINE_DAYS,
    MAX_TIMELINE_RIDES,
    ArchivedRide,
    ArchiveTimeline,
    TimelineLimitError,
    latest_query,
    timeline_query,
)
from .const import BUSINESS_TIMEZONE, MAX_RESPONSE_BYTES
from .detail_codec import ArchivedDetail, CorruptPayload, packed, scoped_ride, unpacked
from .detail_health import HistoryHealth
from .exceptions import NinebotError
from .history_plan import HistoryPlan
from .history_progress import HistoryEvidence
from .history_store import PageReceipt
from .local_reconstruction import Reconstruction
from .models import TravelMonth, VehicleProfile
from .object_checks import ObjectSummary
from .parsing import previous_month
from .raw import Endpoint, RawRecord, version_metadata
from .reconstruction_job import ReconstructionJob
from .ride_models import Ride, TrackQuality
from .statistics_store import StoredMonth, StoredRide
from .sync_job import SyncJob, SyncState, job_data, restored_job
from .travel import MAX_RIDES, opaque_id, parse_ride, select_geometry

if TYPE_CHECKING:
    from .month_tasks import MonthTask
    from .preservation import PreservationTask

SCHEMA_VERSION = 5
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_BYTES = 32 * 1024 * 1024
MAX_PENDING = 16
MAX_PAGE = 100
MAX_PROFILES = 128
T = TypeVar("T")


class ArchiveFailure(StrEnum):
    CLOSED = "closed"
    BUSY = "busy"
    CAPACITY = "capacity"
    STORAGE = "storage"
    SCHEMA = "schema"
    OWNER = "owner"
    CURSOR = "cursor"
    BUDGET = "budget"
    OBJECT = "corrupt_object"


class ArchiveError(Exception):
    """Fixed safe error kinds; never expose SQLite paths or row contents."""

    def __init__(self, kind: ArchiveFailure) -> None:
        self.kind = kind
        super().__init__(kind.value)


@dataclass(frozen=True)
class ArchiveMonth:
    travel: TravelMonth
    received_at: datetime
    revision: int
    backend_version: str | None
    returned_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    known_ride_count: int
    statistics_rides: tuple[Ride, ...] | None = None

    @property
    def complete(self) -> bool:
        return (
            self.travel.summary is not None
            and self.travel.summary.list_complete is True
            and not self.missing_ids
        )


@dataclass(frozen=True)
class ArchiveCursor:
    """Revision-fenced keyset, bound to this account, vehicle and query range."""

    owner: str
    vehicle: str
    revision: int
    start: str | None
    end: str | None
    after_start: str
    after_id: str


@dataclass(frozen=True)
class ArchivePage:
    rides: tuple[Ride, ...]
    revision: int
    next_cursor: ArchiveCursor | None


@dataclass(frozen=True)
class ArchiveProjection:
    """One vehicle's month facts and latest ride from a single actor read."""

    months: tuple[ArchiveMonth, ...]
    latest: ArchivedRide | None
    revision: int
    latest_limited: bool = False


def vehicle_key(sn: str) -> str:
    if not isinstance(sn, str) or opaque_id(sn) != sn:
        raise ValueError("Invalid vehicle identity")
    return hashlib.sha256(sn.encode()).hexdigest()


def utc_string(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Archive timestamp must be aware")
    result = value.astimezone(UTC).isoformat(timespec="microseconds")
    stamp(result)
    return result


SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE vehicles (vehicle TEXT PRIMARY KEY, profile TEXT, observed TEXT NOT"
    " NULL, present INTEGER NOT NULL CHECK (present IN (0,1)))",
    "CREATE TABLE months (vehicle TEXT NOT NULL REFERENCES vehicles(vehicle), month "
    "TEXT NOT NULL, data TEXT NOT NULL, observed TEXT NOT NULL, revision INTEGER NOT "
    "NULL, backend TEXT, ids TEXT NOT NULL, PRIMARY KEY(vehicle,month))",
    "CREATE TABLE rides (vehicle TEXT NOT NULL REFERENCES vehicles(vehicle), id TEXT "
    "NOT NULL, data TEXT NOT NULL, observed TEXT NOT NULL, revision INTEGER NOT NULL,"
    " started TEXT NOT NULL, ended TEXT, PRIMARY KEY(vehicle,id))",
    "CREATE TABLE membership (vehicle TEXT NOT NULL, month TEXT NOT NULL, ride TEXT "
    "NOT NULL, latest INTEGER NOT NULL CHECK (latest IN (0,1)), PRIMARY "
    "KEY(vehicle,month,ride), FOREIGN KEY(vehicle,month) REFERENCES "
    "months(vehicle,month), FOREIGN KEY(vehicle,ride) REFERENCES rides(vehicle,id))",
    "CREATE INDEX rides_start ON rides(vehicle,started,id)",
    "CREATE INDEX rides_end ON rides(vehicle,ended)",
)
TABLE_COLUMNS = {
    "meta": ("key", "value"),
    "vehicles": ("vehicle", "profile", "observed", "present"),
    "months": ("vehicle", "month", "data", "observed", "revision", "backend", "ids"),
    "rides": ("vehicle", "id", "data", "observed", "revision", "started", "ended"),
    "membership": ("vehicle", "month", "ride", "latest"),
}


DETAIL_SCHEMA = (
    "CREATE TABLE detail_payloads (vehicle TEXT NOT NULL, ride TEXT NOT NULL, hash TEXT NOT NULL,"
    " body BLOB NOT NULL, observed TEXT NOT NULL, month TEXT NOT NULL, backend TEXT, endpoint TEXT,"
    " PRIMARY KEY(vehicle,ride,hash), FOREIGN KEY(vehicle,ride) REFERENCES rides(vehicle,id))",
    "CREATE TABLE details (vehicle TEXT NOT NULL, ride TEXT NOT NULL, latest_hash TEXT NOT NULL,"
    " geometry_hash TEXT NOT NULL, observed TEXT NOT NULL, revision INTEGER NOT NULL,"
    " selection TEXT NOT NULL, PRIMARY KEY(vehicle,ride),"
    " FOREIGN KEY(vehicle,ride,latest_hash) REFERENCES detail_payloads(vehicle,ride,hash),"
    " FOREIGN KEY(vehicle,ride,geometry_hash) REFERENCES detail_payloads(vehicle,ride,hash))",
)
DETAIL_COLUMNS = {
    "detail_payloads": (
        "vehicle",
        "ride",
        "hash",
        "body",
        "observed",
        "month",
        "backend",
        "endpoint",
    ),
    "details": (
        "vehicle",
        "ride",
        "latest_hash",
        "geometry_hash",
        "observed",
        "revision",
        "selection",
    ),
}


class RideArchive:
    """One connection and writer thread per entry; bounded coroutine admission."""

    def __init__(self, path: Path, owner: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if len(owner) != 64 or any(char not in "0123456789abcdef" for char in owner):
            raise ValueError("Invalid archive owner scope")
        if type(max_bytes) is not int or not 32768 <= max_bytes <= MAX_ARCHIVE_BYTES:
            raise ValueError("Invalid archive capacity")
        self.path = path
        self.owner = owner
        self.max_bytes = max_bytes
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ninebot-archive")
        self._connection: sqlite3.Connection | None = None
        self._pending = 0
        self._closing = False
        self._opened = False
        self._open_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self.write_paused = False
        self.backup_paused = False
        self._space_checked_at = 0.0
        self._recovery_seals: dict[str, tuple[tuple[int, int, int], float]] = {}

    async def _run(
        self,
        operation: Callable[[], T],
        *,
        lifecycle: bool = False,
        guard: Callable[[], bool] | None = None,
    ) -> T:
        if not lifecycle and (self._closing or not self._opened):
            raise ArchiveError(ArchiveFailure.CLOSED)
        if not lifecycle and self._pending >= MAX_PENDING:
            raise ArchiveError(ArchiveFailure.BUSY)
        self._pending += 1
        loop = asyncio.get_running_loop()

        def guarded() -> T:
            if guard is not None:
                permitted: Future[bool] = Future()

                def check() -> None:
                    try:
                        permitted.set_result(not self._closing and guard())
                    except Exception:
                        permitted.set_result(False)

                # Ownership is evaluated on HA's loop after this operation
                # reaches the front of the worker queue, never in SQL's thread.
                loop.call_soon_threadsafe(check)
                try:
                    allowed = permitted.result(timeout=5)
                except WorkerTimeout:
                    allowed = False
                if not allowed:
                    raise ArchiveError(ArchiveFailure.OWNER)
            return operation()

        future = loop.run_in_executor(self._executor, guarded)
        cancelled = False
        try:
            # Cancellation cannot let the next writer or shutdown overtake a
            # transaction that continues in the executor after its caller exits.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                if not future.cancelled():
                    future.exception()  # Consume any worker failure before propagation.
                raise asyncio.CancelledError
            return future.result()
        except sqlite3.DatabaseError as err:
            if getattr(err, "sqlite_errorcode", 0) & 255 == sqlite3.SQLITE_FULL:
                self.write_paused = True
                raise ArchiveError(ArchiveFailure.CAPACITY) from None
            raise ArchiveError(ArchiveFailure.STORAGE) from None
        except OSError:
            raise ArchiveError(ArchiveFailure.STORAGE) from None
        except CorruptPayload:
            raise ArchiveError(ArchiveFailure.OBJECT) from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ArchiveError(ArchiveFailure.SCHEMA) from None
        finally:
            self._pending -= 1

    def _db(self, *, write: bool = False) -> sqlite3.Connection:
        if self._connection is None:
            raise ArchiveError(ArchiveFailure.CLOSED)
        if write and self.write_paused:
            raise ArchiveError(ArchiveFailure.CAPACITY)
        if write and self.backup_paused:
            raise ArchiveError(ArchiveFailure.BUSY)
        if write and time.monotonic() - self._space_checked_at >= 30:
            if shutil.disk_usage(self.path.parent).free < MIN_FREE_BYTES:
                self.write_paused = True
                raise ArchiveError(ArchiveFailure.CAPACITY)
            self._space_checked_at = time.monotonic()
        return self._connection

    async def async_prepare_backup(self) -> None:
        """Reject new writes immediately, then drain earlier SQL operations."""
        self.backup_paused = True
        if self._closing:
            await self.async_close()
            return

        def barrier() -> None:
            if self._connection is not None and self._connection.in_transaction:
                raise ArchiveError(ArchiveFailure.STORAGE)

        await self._run(barrier, lifecycle=True)

    def resume_after_backup(self) -> None:
        """The loop clears a transient barrier, never a capacity failure."""
        self.backup_paused = False

    async def async_set_capacity(self, max_bytes: int, *, guard: Callable[[], bool]) -> bool:
        """Recheck space/quota without deleting data or bypassing backup fencing."""
        if type(max_bytes) is not int or not 32768 <= max_bytes <= MAX_ARCHIVE_BYTES:
            raise ValueError("Invalid archive capacity")

        def update() -> bool:
            if self.backup_paused:
                raise ArchiveError(ArchiveFailure.BUSY)
            db = self._db()
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            budget = max_bytes // page_size
            db.execute(f"PRAGMA max_page_count={max(pages, budget)}")
            self.max_bytes = max_bytes
            self.write_paused = (
                pages > budget or shutil.disk_usage(self.path.parent).free < MIN_FREE_BYTES
            )
            self._space_checked_at = time.monotonic()
            return not self.write_paused

        return await self._run(update, guard=guard)

    async def async_open(self) -> None:
        async with self._open_lock:
            if self.backup_paused:
                raise ArchiveError(ArchiveFailure.BUSY)
            if self._closing or self._opened:
                raise ArchiveError(ArchiveFailure.CLOSED)
            try:
                await self._run(self._open, lifecycle=True)
            except asyncio.CancelledError:
                # _run waits for its worker before propagating cancellation.
                # Do not reopen/leak a successfully created connection.
                self._opened = self._connection is not None
                raise
            self._opened = True

    def _check(
        self,
        db: sqlite3.Connection,
        version: int = SCHEMA_VERSION,
        *,
        allow_time_index_drift: bool = False,
        allow_missing_detail_payloads: bool = False,
    ) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] != version:
            raise ArchiveError(ArchiveFailure.SCHEMA)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        from .history_store import COLUMNS as HISTORY_COLUMNS
        from .history_store import LEGACY_COLUMNS
        from .object_checks import COLUMNS as CHECK_COLUMNS

        columns_by_table = (
            TABLE_COLUMNS
            | (DETAIL_COLUMNS if version >= 2 else {})
            | (HISTORY_COLUMNS if version >= 4 else LEGACY_COLUMNS if version == 3 else {})
            | (CHECK_COLUMNS if version >= 5 else {})
        )
        if tables != columns_by_table.keys():
            raise ArchiveError(ArchiveFailure.SCHEMA)
        for table, columns in columns_by_table.items():
            if tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})")) != columns:
                raise ArchiveError(ArchiveFailure.SCHEMA)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        if meta.get("owner") != self.owner:
            raise ArchiveError(ArchiveFailure.OWNER)
        if not str(meta.get("revision", "")).isdigit():
            raise ArchiveError(ArchiveFailure.SCHEMA)
        from .preservation import task_state

        for key, value in meta.items():
            if key.startswith("history_reuse:"):
                from .history_reuse import check as check_reuse

                check_reuse(db, key, value)
            elif key.startswith("preservation_detail:"):
                from .detail_followup import check as check_followup

                check_followup(key, value)
            elif key.startswith("payload_search:"):
                from .recovery_search import check as check_recovery_search

                check_recovery_search(key, value)
            elif key.startswith("local_reconstruction:"):
                from .reconstruction_job import restored as restored_reconstruction
                from .reconstruction_job import scope as reconstruction_scope

                reconstruction_scope(key.removeprefix("local_reconstruction:"))
                restored_reconstruction(value)
            elif key.startswith(("history_audit:", "history_audit_fault:")):
                from .archive_audit import validate_meta

                validate_meta(key, value)
            elif key == "reconstruction_vehicle":
                from .reconstruction_job import scope as reconstruction_scope

                reconstruction_scope(value)
            elif key == "history_audit_vehicle":
                from .archive_audit import scope as audit_scope

                audit_scope(value)
            elif key.startswith("history_delta_seen:"):
                from .archive_audit import scope as audit_scope
                from .payload_inventory import validate as validate_inventory

                audit_scope(key.removeprefix("history_delta_seen:"))
                parts = value.split(":")
                if len(parts) != 2:
                    raise ValueError("Invalid delta receipt fence")
                validate_inventory(parts[0])
                if not parts[1].isdigit() or not 1 <= int(parts[1]) <= 2**31 - 1:
                    raise ValueError("Invalid delta receipt contract")
            elif key.startswith("history_payload_revision:"):
                from .archive_audit import scope as audit_scope
                from .payload_inventory import validate as validate_inventory

                audit_scope(key.removeprefix("history_payload_revision:"))
                validate_inventory(value)
            elif key == "history_payload_revision":
                from .payload_inventory import validate as validate_inventory

                validate_inventory(value)
            elif key == "history_family_turn" and value not in {"0", "1", "2"}:
                raise ValueError("Invalid history family turn")
            elif key.startswith("history_incremental_seen:") or key.startswith(
                "history_run_signature:"
            ):
                if (
                    version < 4
                    or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)
                ):
                    raise ValueError("Invalid incremental signature")
                _, vehicle, scope = key.split(":", 2)
                if len(vehicle) != 64 or any(c not in "0123456789abcdef" for c in vehicle):
                    raise ValueError("Invalid incremental signature scope")
                if key.startswith("history_incremental_seen:"):
                    previous_month(scope)
                elif len(scope) != 32 or any(c not in "0123456789abcdef" for c in scope):
                    raise ValueError("Invalid run signature scope")
                elif key.startswith("history_run_signature:"):
                    from .incremental_runs import get

                    if get(db, vehicle, scope) is None:
                        raise ValueError("Missing signed incremental run")
            elif key.startswith("history_run_active:"):
                if version < 4:
                    raise ValueError("Unsupported incremental evidence version")
                from .incremental_runs import validate_active

                validate_active(db, key, value)
            elif key.startswith("history_run:"):
                if version < 4:
                    raise ValueError("Unsupported incremental evidence version")
                from .incremental_runs import validate_key

                validate_key(key, value)
            elif key.startswith(("history_paused:", "history_requested:")):
                vehicle = key.split(":", 1)[1]
                if (
                    len(vehicle) != 64
                    or any(c not in "0123456789abcdef" for c in vehicle)
                    or value not in {"0", "1"}
                ):
                    raise ValueError("Invalid history pause state")
            elif key.startswith(("history_last_success:", "history_last_commit:")):
                vehicle = key.split(":", 1)[1]
                if len(vehicle) != 64 or any(c not in "0123456789abcdef" for c in vehicle):
                    raise ValueError("Invalid history success scope")
                stamp(value)
            elif key.startswith("detail_health:"):
                from .detail_health import key as health_key
                from .detail_health import validate as validate_health

                _, vehicle, identity = key.split(":", 2)
                if health_key(vehicle, identity) != key:
                    raise ValueError("Invalid detail health scope")
                validate_health(value)
            elif key.startswith("month_task:"):
                from .month_tasks import validate_key

                validate_key(key, value)
            elif key == "preservation_month_kind" and value not in {"sparse", "range"}:
                raise ValueError("Invalid month scheduling turn")
            elif key.startswith("preservation:"):
                task_state(value)
            elif key == "preservation_cursor" and (
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("Invalid preservation cursor")
            elif key == "preservation_turn" and value not in {"detail", "month"}:
                raise ValueError("Invalid preservation turn")
            elif key == "preservation_budget" and not 0 <= float(value) <= 4102444800:
                raise ValueError("Invalid preservation budget timestamp")
        from .month_tasks import MAX_TASKS

        if sum(key.startswith("month_task:") for key in meta) > MAX_TASKS:
            raise ValueError("Invalid month task count")
        if "sync_job" in meta:
            restored_job(decoded(meta["sync_job"]))
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ArchiveError(ArchiveFailure.SCHEMA)
        failures = db.execute("PRAGMA foreign_key_check")
        for table, _, parent, _ in failures:
            if not (
                allow_missing_detail_payloads
                and version == SCHEMA_VERSION
                and table == "details"
                and parent == "detail_payloads"
            ):
                raise ArchiveError(ArchiveFailure.SCHEMA)
        # Read-only validation before any PRAGMA writes/migration. Disk facts
        # must pass the same strict codec as new observations.
        for vehicle, profile, observed, present in db.execute("SELECT * FROM vehicles"):
            if (
                len(vehicle) != 64
                or any(char not in "0123456789abcdef" for char in vehicle)
                or present not in (0, 1)
            ):
                raise ValueError("Invalid archived vehicle")
            stamp(observed)
            if (
                profile is not None
                and vehicle_key(restored_profile(decoded(profile)).sn) != vehicle
            ):
                raise ValueError("Invalid archived profile scope")
        for _, key, data, observed, revision, backend, ids in db.execute("SELECT * FROM months"):
            previous_month(key)
            if (
                restored_month(decoded(data)).month != key
                or revision < 1
                or (backend is not None and version_metadata(backend) != backend)
            ):
                raise ValueError("Invalid archived month")
            stamp(observed)
            identities = self._ids(ids)
            summary = restored_month(decoded(data)).summary
            if summary is None or summary.unique_ride_count != len(identities):
                raise ValueError("Invalid archived month membership")
        for _, key, data, observed, revision, started, ended in db.execute("SELECT * FROM rides"):
            ride = restored_ride(decoded(data))
            if (
                ride.ride_id != key
                or revision < 1
                or (
                    not allow_time_index_drift
                    and (
                        started != (utc_string(ride.started_at) if ride.started_at else "")
                        or ended != (utc_string(ride.ended_at) if ride.ended_at else None)
                    )
                )
            ):
                raise ValueError("Invalid archived ride index")
            stamp(observed)

        if version >= 5:
            from .object_checks import check as check_objects

            check_objects(db)

        if version >= 3:
            from .history_store import check as check_history

            check_history(db, allow_runs=version >= 4)

        if version >= 2:
            for (
                vehicle,
                identity,
                digest,
                body_type,
                body_size,
                observed,
                month,
                backend,
                endpoint,
            ) in db.execute(
                "SELECT vehicle,ride,hash,typeof(body),length(body),observed,month,"
                "backend,endpoint "
                "FROM detail_payloads"
            ):
                if (
                    len(vehicle) != 64
                    or opaque_id(identity) != identity
                    or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or body_type != "blob"
                    or body_size < 0
                    or (backend is not None and version_metadata(backend) != backend)
                    or (endpoint is not None and version_metadata(endpoint) != endpoint)
                ):
                    raise ValueError("Invalid private detail metadata")
                stamp(observed)
                previous_month(month)
            for _, _, _, _, observed, revision, selection in db.execute("SELECT * FROM details"):
                if revision < 1 or selection not in {
                    "initial",
                    "accepted_candidate",
                    "retained_previous_missing_geometry",
                    "retained_previous_server_simplified",
                    "retained_previous_geometry_conflict",
                    "retained_previous_unverified_reduction",
                }:
                    raise ValueError("Invalid private selection metadata")
                stamp(observed)
            if db.execute(
                "SELECT 1 FROM detail_payloads GROUP BY vehicle,ride HAVING COUNT(*)>2 LIMIT 1"
            ).fetchone():
                raise ValueError("Invalid private detail retention")

    def _open(self) -> None:
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ArchiveError(ArchiveFailure.STORAGE)
        if self.path.exists():
            probe = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            try:
                version = probe.execute("PRAGMA user_version").fetchone()[0]
                if version not in (1, 2, 3, 4, SCHEMA_VERSION):
                    raise ArchiveError(ArchiveFailure.SCHEMA)
                missing_details = False
                try:
                    self._check(probe, version)
                except ArchiveError as err:
                    if err.kind is not ArchiveFailure.SCHEMA or version != SCHEMA_VERSION:
                        raise
                    # Every other structural, owner and normalized check remains strict.
                    self._check(probe, version, allow_missing_detail_payloads=True)
                    missing_details = True
            finally:
                probe.close()
            if missing_details:
                from .startup_recovery import recover

                if not recover(
                    self.path,
                    self.owner,
                    self._check,
                    minimum_free=MIN_FREE_BYTES,
                    max_bytes=self.max_bytes,
                ):
                    raise ArchiveError(ArchiveFailure.SCHEMA)
            if version < SCHEMA_VERSION:
                self._migrate(version)
        else:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            db = sqlite3.connect(self.path)
            try:
                with db:
                    from .history_store import SCHEMA as HISTORY_SCHEMA
                    from .object_checks import SCHEMA as CHECK_SCHEMA

                    for sql in (*SCHEMA, *DETAIL_SCHEMA, *HISTORY_SCHEMA, *CHECK_SCHEMA):
                        db.execute(sql)
                    db.executemany(
                        "INSERT INTO meta VALUES (?,?)", (("owner", self.owner), ("revision", "0"))
                    )
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            finally:
                db.close()
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            budget = self.max_bytes // page_size
            self.write_paused = pages > budget
            db.execute(f"PRAGMA max_page_count={max(pages, budget)}")
            os.chmod(self.path, 0o600)
            self._connection = db
        except BaseException:
            db.close()
            raise

    def _migrate(self, version: int) -> None:
        """Validate first, save an exclusive consistent backup, then atomic DDL."""
        if shutil.disk_usage(self.path.parent).free < self.path.stat().st_size * 2 + MIN_FREE_BYTES:
            raise ArchiveError(ArchiveFailure.CAPACITY)
        with self.path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        destination = self.path.with_name(f"rides.schema{version}-{digest}.sqlite3")
        db = sqlite3.connect(self.path)
        try:
            if not destination.exists():
                descriptor, filename = tempfile.mkstemp(
                    prefix=".ninebot-migration-", dir=self.path.parent
                )
                os.close(descriptor)
                temporary = Path(filename)
                try:
                    target = sqlite3.connect(temporary)
                    try:
                        db.backup(target)
                        self._check(target, version)
                    finally:
                        target.close()
                    with temporary.open("rb") as stream:
                        os.fsync(stream.fileno())
                    os.link(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            elif destination.is_symlink():
                raise ArchiveError(ArchiveFailure.STORAGE)
            else:
                target = sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)
                try:
                    self._check(target, version)
                finally:
                    target.close()
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={max(pages, self.max_bytes // size)}")
            db.execute("BEGIN IMMEDIATE")
            try:
                from .history_store import PAYLOAD_SCHEMA
                from .history_store import SCHEMA as HISTORY_SCHEMA
                from .object_checks import SCHEMA as CHECK_SCHEMA

                for sql in (
                    *(DETAIL_SCHEMA if version == 1 else ()),
                    *(HISTORY_SCHEMA if version < 3 else PAYLOAD_SCHEMA if version == 3 else ()),
                    *CHECK_SCHEMA,
                ):
                    db.execute(sql)
                # Preserve cursors/counts, but don't promote a pre-schema5 scan
                # into a certificate for the new stable-identity receipt store.
                from .archive_audit import put as put_audit
                from .archive_audit import restored as restored_audit

                for key, value in db.execute(
                    "SELECT key,value FROM meta WHERE key LIKE 'history_audit:%'"
                ).fetchall():
                    put_audit(
                        db,
                        key.removeprefix("history_audit:"),
                        replace(restored_audit(value), changed=True),
                    )
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._check(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        finally:
            db.close()

    @staticmethod
    def _ids(value: str) -> tuple[str, ...]:
        result = decoded(value)
        ids = result.get("ids")
        if (
            set(result) != {"ids"}
            or not isinstance(ids, list)
            or len(ids) > MAX_RIDES
            or any(not isinstance(item, str) or opaque_id(item) != item for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Invalid archive membership")
        return tuple(ids)

    def _revision(self, *, bump: bool = False) -> int:
        db = self._db()
        value = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        if bump:
            value += 1
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(value),))
        return value

    async def async_profiles(self) -> tuple[VehicleProfile, ...]:
        return await self._run(
            lambda: tuple(
                restored_profile(decoded(row[0]))
                for row in self._db().execute(
                    "SELECT profile FROM vehicles WHERE present=1 AND profile IS NOT "
                    "NULL ORDER BY vehicle"
                )
            )
        )

    async def async_record_profiles(
        self,
        profiles: tuple[VehicleProfile, ...],
        received: datetime,
        *,
        complete: bool,
        guard: Callable[[], bool] | None = None,
        ordered: bool = False,
    ) -> None:
        if type(ordered) is not bool or (ordered and guard is None):
            raise ValueError("Ordered receipts require an ownership/revision fence")
        await self._run(
            partial(self._record_profiles, profiles, utc_string(received), complete, ordered),
            guard=guard,
        )

    def _record_profiles(
        self, profiles: tuple[VehicleProfile, ...], observed: str, complete: bool, ordered: bool
    ) -> None:
        if type(complete) is not bool or len(profiles) > MAX_PROFILES:
            raise ValueError("Invalid archive profiles")
        data = [
            (
                vehicle_key(profile.sn),
                encoded(profile_data(restored_profile(profile_data(profile)))),
            )
            for profile in profiles
        ]
        if len({key for key, _ in data}) != len(data):
            raise ValueError("Duplicate archive profiles")
        db = self._db(write=True)
        with db:
            previous = db.execute("SELECT value FROM meta WHERE key='profiles_observed'").fetchone()
            if previous and (previous[0] > observed or (previous[0] == observed and not ordered)):
                return
            if complete:
                db.execute("UPDATE vehicles SET present=0 WHERE observed<=?", (observed,))
            for key, profile in data:
                db.execute(
                    "INSERT INTO vehicles VALUES (?,?,?,1) ON CONFLICT(vehicle) DO "
                    "UPDATE SET profile=excluded.profile, observed=excluded.observed,"
                    " present=1",
                    (key, profile, observed),
                )
            db.execute(
                "INSERT INTO meta VALUES ('profiles_observed',?) ON CONFLICT(key) DO "
                "UPDATE SET value=excluded.value",
                (observed,),
            )

    def _ride(self, vehicle: str, identity: str) -> tuple[Ride, str, int] | None:
        row = (
            self._db()
            .execute(
                "SELECT data,observed,revision FROM rides WHERE vehicle=? AND id=?",
                (vehicle, identity),
            )
            .fetchone()
        )
        return (restored_ride(decoded(row[0])), row[1], row[2]) if row else None

    def _upsert_ride(
        self, vehicle: str, incoming: Ride, observed: str, ordered: bool = False
    ) -> bool:
        if incoming.ride_id is None:
            return False  # Explicit month coverage preserves missing identity.
        before = self._ride(vehicle, incoming.ride_id)
        if before and (before[1] > observed or (before[1] == observed and not ordered)):
            return False
        result = merged_ride(before[0] if before else None, incoming)
        data = encoded(ride_data(result))
        changed = not before or data != encoded(ride_data(before[0]))
        revision = (before[2] if before else 0) + int(changed)
        self._db().execute(
            "INSERT INTO rides VALUES (?,?,?,?,?,?,?) ON CONFLICT(vehicle,id) DO "
            "UPDATE SET "
            "data=excluded.data,observed=excluded.observed,revision=excluded.revision,started=excluded.started,ended=excluded.ended",
            (
                vehicle,
                incoming.ride_id,
                data,
                observed,
                revision,
                utc_string(result.started_at) if result.started_at else "",
                utc_string(result.ended_at) if result.ended_at else None,
            ),
        )
        return changed

    async def async_record_month(
        self,
        sn: str,
        travel: TravelMonth,
        received: datetime,
        backend_version: str | None = None,
        *,
        guard: Callable[[], bool] | None = None,
        ordered: bool = False,
    ) -> bool:
        if type(ordered) is not bool or (ordered and guard is None):
            raise ValueError("Ordered receipts require an ownership/revision fence")
        return await self._run(
            partial(
                self._record_month,
                vehicle_key(sn),
                travel,
                utc_string(received),
                backend_version,
                ordered,
            ),
            guard=guard,
        )

    def _record_month(
        self,
        vehicle: str,
        travel: TravelMonth,
        observed: str,
        backend: str | None,
        ordered: bool,
        *,
        sync_checkpoint: bool = True,
    ) -> bool:
        # All candidate validation is before the transaction. No raw objects
        # are passed through encoded month data or normalized ride rows.
        data = encoded(month_data(travel))
        restored_month(decoded(data))
        if len(travel.rides) > MAX_RIDES or (
            backend is not None and version_metadata(backend) != backend
        ):
            raise ValueError("Invalid archive month metadata")
        ids = tuple(
            dict.fromkeys(ride.ride_id for ride in travel.rides if ride.ride_id is not None)
        )
        ids_data = encoded({"ids": ids})
        self._ids(ids_data)
        if (
            travel.summary is None
            or travel.summary.returned_count != len(travel.rides)
            or travel.summary.unique_ride_count != len(ids)
        ):
            raise ValueError("Archive month membership mismatch")
        candidates: dict[str, Ride] = {}
        for ride in travel.rides:
            restored_ride(decoded(encoded(ride_data(ride))))
            if ride.query_month != travel.month:
                raise ValueError("Archive source month mismatch")
            if ride.ride_id:
                if ride.ride_id in candidates and ride_data(candidates[ride.ride_id]) != ride_data(
                    ride
                ):
                    raise ValueError("Conflicting duplicate identity")
                candidates[ride.ride_id] = ride
        db = self._db(write=True)
        with nullcontext() if db.in_transaction else db:
            before = db.execute(
                "SELECT data,observed,revision,backend,ids FROM months WHERE vehicle=? AND month=?",
                (vehicle, travel.month),
            ).fetchone()
            if before and (before[1] > observed or (before[1] == observed and not ordered)):
                return False
            changed = not before or (before[0], before[3], before[4]) != (data, backend, ids_data)
            db.execute("INSERT OR IGNORE INTO vehicles VALUES (?,NULL,?,0)", (vehicle, observed))
            revision = (before[2] if before else 0) + int(changed)
            db.execute(
                "INSERT INTO months VALUES (?,?,?,?,?,?,?) ON CONFLICT(vehicle,month)"
                " DO UPDATE SET "
                "data=excluded.data,observed=excluded.observed,revision=excluded.revision,backend=excluded.backend,ids=excluded.ids",
                (vehicle, travel.month, data, observed, revision, backend, ids_data),
            )
            db.execute(
                "UPDATE membership SET latest=0 WHERE vehicle=? AND month=?",
                (vehicle, travel.month),
            )
            for identity, ride in candidates.items():
                changed = self._upsert_ride(vehicle, ride, observed, ordered) or changed
                db.execute(
                    "INSERT INTO membership VALUES (?,?,?,1) ON "
                    "CONFLICT(vehicle,month,ride) DO UPDATE SET latest=1",
                    (vehicle, travel.month, identity),
                )
            if changed:
                self._revision(bump=True)
            # Facts and the job checkpoint commit or roll back together.
            job = self._sync_job()
            if (
                sync_checkpoint
                and job is not None
                and job.state is SyncState.QUERYING
                and job.vehicle == vehicle
                and job.next_month == travel.month
            ):
                assert travel.summary is not None
                self._put_sync(
                    job.advance(
                        max(observed, job.updated_at),
                        queried=True,
                        complete=travel.summary.list_complete,
                    )
                )
        return changed

    async def async_record_detail(
        self,
        sn: str,
        ride: Ride,
        received: datetime,
        *,
        guard: Callable[[], bool] | None = None,
        ordered: bool = False,
        raw: RawRecord | None = None,
    ) -> bool:
        if type(ordered) is not bool or (ordered and guard is None):
            raise ValueError("Ordered receipts require an ownership/revision fence")
        return await self._run(
            partial(self._record_detail, vehicle_key(sn), ride, utc_string(received), ordered, raw),
            guard=guard,
        )

    def _history_committed(self, vehicle: str, observed: str) -> None:
        db = self._db()
        key = f"history_last_commit:{vehicle}"
        prior = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if prior is None or stamp(observed) > stamp(prior[0]):
            db.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, observed),
            )

    def _record_detail(
        self, vehicle: str, ride: Ride, observed: str, ordered: bool, raw: RawRecord | None = None
    ) -> bool:
        # A detail call cannot introduce an arbitrary ID not already indexed
        # for this vehicle/month, even when another account owns that ID.
        db = self._db(write=True)
        if (
            ride.ride_id is None
            or not db.execute(
                "SELECT 1 FROM membership WHERE vehicle=? AND month=? AND ride=?",
                (vehicle, ride.query_month, ride.ride_id),
            ).fetchone()
        ):
            raise ArchiveError(ArchiveFailure.OWNER)
        before = self._ride(vehicle, ride.ride_id)
        if before is None or before[0].detail_id is None or before[0].detail_id != ride.detail_id:
            raise ArchiveError(ArchiveFailure.OWNER)
        if before[1] > observed or (before[1] == observed and not ordered):
            return False
        with db:
            changed = self._upsert_ride(vehicle, ride, observed, ordered)
            if raw is not None:
                changed = self._save_private_detail(vehicle, ride, raw, observed) or changed
                if db.execute(
                    "SELECT observed FROM details WHERE vehicle=? AND ride=?",
                    (vehicle, ride.ride_id),
                ).fetchone() == (observed,):
                    self._history_committed(vehicle, observed)
            if changed:
                self._revision(bump=True)
        return changed

    def _save_private_detail(self, vehicle: str, ride: Ride, raw: RawRecord, observed: str) -> bool:
        if raw.query_month != ride.query_month or utc_string(raw.received_at) != observed:
            raise ValueError("Invalid detail receipt")
        digest, body, clean = packed(raw)
        candidate = parse_ride(
            clean.payload(), ride.query_month, source=Endpoint.TRIP_DETAIL, max_points=2000
        )
        for field in ("started_at", "ended_at"):
            if getattr(candidate, field) is not None and getattr(candidate, field) != getattr(
                ride, field
            ):
                raise ValueError("Invalid detail trip scope")
        if (
            (candidate.ride_id is not None and candidate.ride_id != ride.ride_id)
            or (candidate.detail_id is not None and candidate.detail_id != ride.detail_id)
            or {"conflicting_ride_ids", "conflicting_detail_ids"} & set(candidate.issues)
        ):
            raise ValueError("Invalid private detail identity")
        db = self._db()
        old = self._private_detail(vehicle, ride.ride_id, 2000)
        assert old is None or old.latest is not None  # Internal writers require strict reads.
        if old and old.latest is not None and utc_string(old.latest.received_at) > observed:
            return False
        adopt, reason = select_geometry(old.ride, candidate) if old else (True, "initial")
        geometry_hash = digest if adopt or old is None else old.geometry_hash
        geometry_quality = (
            candidate.track_quality if adopt or old is None else old.ride.track_quality
        )
        changed = old is None or old.latest_hash != digest or old.geometry_hash != geometry_hash
        revision = (old.revision if old else 0) + int(changed)
        inserted = db.execute(
            "INSERT OR IGNORE INTO detail_payloads VALUES (?,?,?,?,?,?,?,?)",
            (
                vehicle,
                ride.ride_id,
                digest,
                body,
                observed,
                ride.query_month,
                clean.backend_version,
                clean.endpoint_version,
            ),
        )
        db.execute(
            "INSERT INTO details VALUES (?,?,?,?,?,?,?) ON CONFLICT(vehicle,ride) DO UPDATE SET "
            "latest_hash=excluded.latest_hash,geometry_hash=excluded.geometry_hash,"
            "observed=excluded.observed,revision=excluded.revision,selection=excluded.selection",
            (vehicle, ride.ride_id, digest, geometry_hash, observed, revision, reason),
        )
        # Derived receipts for discarded ordinary bodies are no longer current.
        db.execute(
            "DELETE FROM payload_checks WHERE vehicle=? AND kind='detail'"
            " AND identity IN (SELECT json_array(ride,hash) FROM detail_payloads"
            " WHERE vehicle=? AND ride=? AND hash NOT IN (?,?))",
            (vehicle, vehicle, ride.ride_id, digest, geometry_hash),
        )
        # Bound storage to latest candidate plus selected geometry, not every poll.
        removed = db.execute(
            "DELETE FROM detail_payloads WHERE vehicle=? AND ride=? AND hash NOT IN (?,?)",
            (vehicle, ride.ride_id, digest, geometry_hash),
        )
        if inserted.rowcount or removed.rowcount:
            from .payload_inventory import advance as advance_inventory

            advance_inventory(db, vehicle)
        from .detail_health import put as put_health

        assert ride.ride_id is not None
        put_health(
            db,
            vehicle,
            ride.ride_id,
            digest,
            geometry_hash,
            candidate.track_quality,
            geometry_quality,
        )
        return changed

    async def async_reconstruct_ride(
        self,
        sn: str,
        ride_id: str,
        *,
        guard: Callable[[], bool],
        repair: bool = False,
    ) -> Reconstruction:
        """Explicit bounded local repair; backup precedes any derived mutation.

        This primitive does not close Repairs, fix missing source references or
        claim full verification. A resumable job will orchestrate these steps.
        """
        if opaque_id(ride_id) != ride_id or type(repair) is not bool:
            raise ValueError("Invalid reconstruction query")

        loop = asyncio.get_running_loop()

        def reconstruct() -> Reconstruction:
            db, vehicle = self._db(write=repair), vehicle_key(sn)
            result, candidate, observed, quality = self._reconstruction_sources(vehicle, ride_id)
            if not repair or result.status != "repairable":
                return result
            # The one actor prevents another managed writer from changing the
            # evidence between assessment, consistent backup and commit.
            required = self.path.stat().st_size * 2 + MIN_FREE_BYTES
            if shutil.disk_usage(self.path.parent).free < required:
                raise ArchiveError(ArchiveFailure.CAPACITY)
            destination = self.path.with_name(
                f"{self.path.stem}.recovery-{os.urandom(16).hex()}.sqlite3"
            )
            self._backup(destination, recovery=True)
            if not self._worker_owned(loop, guard):
                raise ArchiveError(ArchiveFailure.OWNER)
            with db:
                self._apply_reconstruction(
                    vehicle, ride_id, candidate, observed, quality, result.membership_updates
                )
                # Derived time indexes may be wrong even if normalized JSON
                # matches, so invalidate consumers in either repair case.
                self._revision(bump=True)
            return replace(result, repaired=True)

        return await self._run(reconstruct, guard=guard)

    def _reconstruction_sources(
        self, vehicle: str, ride_id: str
    ) -> tuple[Reconstruction, Ride | None, str | None, str | None]:
        from .local_reconstruction import proposal, quality_patch

        before = self._ride(vehicle, ride_id)
        if before is None:
            return Reconstruction("not_saved"), None, None, None
        try:
            stored = self._private_detail(vehicle, ride_id, 1)
        except CorruptPayload:
            return Reconstruction("corrupt"), None, None, None
        if stored is None:
            return Reconstruction("not_saved"), None, None, None
        result, candidate = proposal(self._db(), vehicle, before[0], stamp(before[1]), stored)
        quality = quality_patch(self._db(), vehicle, stored)
        if quality is not None:
            result = replace(result, status="repairable", fields=result.fields + ("quality_index",))
        result = replace(result, checked_fields=result.checked_fields + ("quality_index",))
        from .membership_recovery import proposal as membership_proposal

        assert stored.latest is not None  # Strict local reconstruction requires both roles.
        try:
            memberships = membership_proposal(
                self._db(), vehicle, before[0], candidate, stored.latest.received_at
            )
        except (CorruptPayload, NinebotError, ValueError, TypeError, KeyError):
            memberships = None
        if memberships is None:
            return replace(result, status="unverifiable"), None, before[1], None
        if memberships:
            result = replace(
                result,
                status="repairable",
                fields=result.fields + ("month_membership",),
                checked_fields=result.checked_fields + ("page_membership_identity",),
                membership_updates=memberships,
            )
        return result, candidate, before[1], quality

    def _apply_reconstruction(
        self,
        vehicle: str,
        ride_id: str,
        candidate: Ride | None,
        observed: str | None,
        quality: str | None,
        memberships: tuple[tuple[str, int], ...] = (),
    ) -> None:
        from .detail_health import key, validate

        if candidate is not None and observed is not None:
            self._upsert_ride(vehicle, candidate, observed, ordered=True)
        if quality is not None:
            validate(quality)
            self._db().execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key(vehicle, ride_id), quality),
            )

        self._db().executemany(
            "INSERT INTO membership VALUES (?,?,?,?)",
            ((vehicle, month, ride_id, latest) for month, latest in memberships),
        )

    def _worker_owned(self, loop: asyncio.AbstractEventLoop, guard: Callable[[], bool]) -> bool:
        permitted: Future[bool] = Future()

        def check() -> None:
            try:
                permitted.set_result(not self._closing and guard())
            except Exception:
                permitted.set_result(False)

        loop.call_soon_threadsafe(check)
        try:
            return permitted.result(timeout=5)
        except WorkerTimeout:
            return False

    def _reconstruction_backup(self, vehicle: str, job: ReconstructionJob) -> str:
        from .reconstruction_job import RecoveryPointError, get

        path = self.path.with_name(f"{self.path.stem}.recovery-{job.job_id}.sqlite3")
        if path.is_symlink():
            raise RecoveryPointError("Unverified local recovery point")
        if not path.exists():
            if job.backup_hash is not None:
                raise RecoveryPointError("Unverified local recovery point")
            if shutil.disk_usage(path.parent).free < self.path.stat().st_size * 2 + MIN_FREE_BYTES:
                raise ArchiveError(ArchiveFailure.CAPACITY)
            self._backup(path, recovery=True)
        try:
            stat = path.stat()
        except OSError:
            raise RecoveryPointError("Unreadable local recovery point") from None
        fingerprint = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        sealed = self._recovery_seals.get(job.job_id)
        if (
            job.backup_hash
            and sealed
            and sealed[0] == fingerprint
            and time.monotonic() - sealed[1] < 86400
        ):
            return job.backup_hash
        if not 0 < stat.st_size <= MAX_ARCHIVE_BYTES:
            raise RecoveryPointError("Unverified local recovery point")
        try:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            raise RecoveryPointError("Unreadable local recovery point") from None
        if job.backup_hash is not None and digest != job.backup_hash:
            raise RecoveryPointError("Unverified local recovery point")
        try:
            target = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        except sqlite3.DatabaseError:
            raise RecoveryPointError("Unreadable local recovery point") from None
        try:
            # Crash between backup publication and recording its hash: reuse
            # only a structurally checked copy carrying this exact job/scope.
            self._check(target, allow_time_index_drift=True)
            captured = get(target, vehicle)
            if captured is None or captured.job_id != job.job_id:
                raise RecoveryPointError("Unverified local recovery scope")
        except (ValueError, sqlite3.DatabaseError, ArchiveError):
            raise RecoveryPointError("Unverified local recovery point") from None
        finally:
            target.close()
        if len(self._recovery_seals) >= MAX_PROFILES:
            self._recovery_seals.clear()
        self._recovery_seals[job.job_id] = (fingerprint, time.monotonic())
        return digest

    async def async_reconstruction_job(
        self, sn: str, operation: str, now: datetime, *, guard: Callable[[], bool]
    ) -> ReconstructionJob | None:
        from .reconstruction_job import RecoveryPointError, get, put, start, step

        if operation not in {"start", "continue", "status", "pause", "cancel", "step"}:
            raise ValueError("Invalid local reconstruction operation")
        utc_string(now)
        loop = asyncio.get_running_loop()

        def run() -> ReconstructionJob | None:
            db, vehicle = self._db(write=operation != "status"), vehicle_key(sn)
            job = get(db, vehicle)
            if operation == "status":
                return job
            if operation == "start":
                with db:
                    return start(db, vehicle, now)
            if job is None or job.terminal:
                return job
            if operation in {"pause", "cancel"}:
                with db:
                    job = replace(
                        job,
                        state="paused" if operation == "pause" else "cancelled",
                        updated_at=now.isoformat(),
                    )
                    put(db, vehicle, job)
                return job
            if job.state == "blocked_recovery" and operation != "continue":
                return job
            if job.state == "paused":
                if operation != "continue":
                    return job
                job = replace(job, state="running" if job.backup_hash else "pending_backup")
            try:
                checksum = self._reconstruction_backup(vehicle, job)
            except RecoveryPointError:
                if not self._worker_owned(loop, guard):
                    raise ArchiveError(ArchiveFailure.OWNER) from None
                with db:
                    job = replace(job, state="blocked_recovery", updated_at=now.isoformat())
                    put(db, vehicle, job)
                return job  # No retry loop or account-wide writer failure.
            if not self._worker_owned(loop, guard):
                raise ArchiveError(ArchiveFailure.OWNER)
            with db:
                job = replace(
                    job, state="running", backup_hash=checksum, updated_at=now.isoformat()
                )
                put(db, vehicle, job)

                def evaluate(ride_id: str) -> Reconstruction:
                    result, candidate, observed, quality = self._reconstruction_sources(
                        vehicle, ride_id
                    )
                    recovered, pending, checks = 0, False, 0
                    if result.status == "corrupt":
                        from .payload_restore import restore

                        recovery = restore(
                            db, self.path, self.owner, vehicle, ride_id, now, job.job_id
                        )
                        recovered, pending, checks = (
                            recovery.recovered,
                            recovery.pending,
                            recovery.examined,
                        )
                        if recovered:
                            result, candidate, observed, quality = self._reconstruction_sources(
                                vehicle, ride_id
                            )
                    if result.status == "repairable":
                        self._apply_reconstruction(
                            vehicle,
                            ride_id,
                            candidate,
                            observed,
                            quality,
                            result.membership_updates,
                        )
                    mutated = result.status == "repairable" or recovered > 0
                    if mutated:
                        self._revision(bump=True)
                    return replace(
                        result,
                        status="searching" if pending else result.status,
                        repaired=bool(mutated),
                        source_copy_checks=checks,
                        fields=result.fields + (("payload_bytes",) if recovered else ()),
                    )

                return step(db, vehicle, now, evaluate)

        return await self._run(run, guard=guard)

    async def async_private_detail(
        self,
        sn: str,
        ride_id: str,
        *,
        max_points: int = 500,
        point_offset: int = 0,
        guard: Callable[[], bool] | None = None,
        allow_partial: bool = False,
    ) -> ArchivedDetail | None:
        if (
            type(allow_partial) is not bool
            or type(point_offset) is not int
            or not 0 <= point_offset <= MAX_RESPONSE_BYTES
            or opaque_id(ride_id) != ride_id
            or type(max_points) is not int
            or not 1 <= max_points <= 2000
        ):
            raise ValueError("Invalid private detail query")
        return await self._run(
            partial(
                self._private_detail,
                vehicle_key(sn),
                ride_id,
                max_points,
                point_offset,
                allow_partial=allow_partial,
            ),
            guard=guard,
        )

    def _private_detail(
        self,
        vehicle: str,
        ride_id: str | None,
        max_points: int,
        point_offset: int = 0,
        *,
        allow_partial: bool = False,
    ) -> ArchivedDetail | None:
        row = (
            self._db()
            .execute(
                "SELECT latest_hash,geometry_hash,observed,revision,selection FROM details "
                "WHERE vehicle=? AND ride=?",
                (vehicle, ride_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        summary = self._ride(vehicle, ride_id) if ride_id is not None else None
        if summary is None:
            raise ValueError("Missing private detail summary")
        records: list[RawRecord | None] = []
        faults: list[str] = []
        decoded_payloads: dict[str, RawRecord | None] = {}
        decoded_qualities: dict[str, TrackQuality] = {}
        for role, digest in zip(("latest", "selected_geometry"), row[:2], strict=True):
            if digest not in decoded_payloads:
                try:
                    blob = (
                        self._db()
                        .execute(
                            "SELECT CASE WHEN length(body)<=? THEN body END,observed,month,"
                            "backend,endpoint"
                            " FROM detail_payloads WHERE vehicle=? AND ride=? AND hash=?",
                            (MAX_RESPONSE_BYTES + 1024, vehicle, ride_id, digest),
                        )
                        .fetchone()
                    )
                    if blob is None:
                        raise CorruptPayload("Missing private payload object")
                    if blob[2] != summary[0].query_month:
                        raise ValueError("Invalid private payload month scope")
                    decoded_record = unpacked(
                        blob[0], digest, stamp(blob[1]), blob[2], blob[3], blob[4]
                    )
                    scoped = scoped_ride(decoded_record, summary[0])
                    decoded_payloads[digest] = decoded_record
                    decoded_qualities[digest] = scoped.track_quality
                except CorruptPayload:
                    decoded_payloads[digest] = None
                    if not allow_partial:
                        raise
            record = decoded_payloads[digest]
            if record is None:
                faults.append(f"{role}_payload_unavailable")
            records.append(record)
        latest, geometry = records
        if latest is None and geometry is None:
            raise CorruptPayload("No usable private payload evidence")
        # Receipt time belongs to the latest object only. Never stamp older
        # selected geometry as if it were the new/latest cloud response.
        if latest is not None:
            latest = replace(latest, received_at=stamp(row[2]))
        geometry_evidence = geometry or latest
        assert geometry_evidence is not None
        selection = row[4] if geometry is not None else "fallback_latest_payload"
        parsed = parse_ride(
            geometry_evidence.payload(),
            geometry_evidence.query_month or summary[0].query_month,
            source=Endpoint.TRIP_DETAIL,
            max_points=max_points,
            point_offset=point_offset,
        )
        result = replace(
            summary[0],
            source=Endpoint.TRIP_DETAIL,
            track_points=parsed.track_points,
            speed_samples=parsed.speed_samples,
            total_track_points=parsed.total_track_points,
            track_truncated=parsed.track_truncated,
            track_quality=parsed.track_quality,
            geometry_selection=selection,
            track_offset=point_offset,
            track_limit=max_points,
        )
        return ArchivedDetail(
            result,
            latest,
            geometry,
            row[3],
            selection,
            row[0],
            row[1],
            summary[2],
            stamp(summary[1]),
            tuple(faults),
            "selected_geometry" if geometry is not None else "latest_payload",
            decoded_qualities.get(row[0]) if latest is not None else None,
            decoded_qualities.get(row[1]) if geometry is not None else None,
        )

    async def async_preservation_order(
        self, vehicles: tuple[str, ...]
    ) -> tuple[tuple[str, ...], str]:
        """Restore fairness without storing account names or raw vehicle identities."""

        def read() -> tuple[tuple[str, ...], str]:
            db = self._db()
            meta = dict(
                db.execute(
                    "SELECT key,value FROM meta WHERE key IN "
                    "('preservation_cursor','preservation_turn')"
                )
            )
            ordered = tuple(sorted(vehicles))
            cursor = meta.get("preservation_cursor")
            for index, sn in enumerate(ordered):
                if vehicle_key(sn) == cursor:
                    ordered = ordered[index + 1 :] + ordered[: index + 1]
                    break
            return ordered, meta.get("preservation_turn", "detail")

        return await self._run(read)

    def _reserve_background(
        self, db: sqlite3.Connection, vehicle: str, epoch: float, budget: int, kind: str
    ) -> bool:
        paused = db.execute(
            "SELECT value FROM meta WHERE key=?", (f"history_paused:{vehicle}",)
        ).fetchone()
        if paused is not None and paused[0] == "1":
            return False
        limit = db.execute("SELECT value FROM meta WHERE key='preservation_budget'").fetchone()
        if limit is not None and float(limit[0]) > epoch:
            return False
        for key, value in (
            ("preservation_budget", str(epoch + 3600 / budget)),
            ("preservation_cursor", vehicle),
            ("preservation_turn", "month" if kind == "detail" else "detail"),
        ):
            db.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        if kind != "detail":
            db.execute(
                "INSERT INTO meta VALUES ('preservation_month_kind',?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                ("range" if kind == "sparse" else "sparse",),
            )
        return True

    async def async_background_budget(
        self, sn: str, now: datetime, budget: int, *, guard: Callable[[], bool]
    ) -> bool:
        """Reserve one month operation before I/O, with the same account detail budget."""
        utc_string(now)
        if type(budget) is not int or not 1 <= budget <= 60:
            raise ValueError("Invalid preservation budget")

        def reserve() -> bool:
            db = self._db(write=True)
            with db:
                return self._reserve_background(
                    db, vehicle_key(sn), now.timestamp(), budget, "month"
                )

        return await self._run(reserve, guard=guard)

    async def async_month_turn(self) -> str:
        def read() -> str:
            found = (
                self._db()
                .execute("SELECT value FROM meta WHERE key='preservation_month_kind'")
                .fetchone()
            )
            return found[0] if found else "sparse"

        return await self._run(read)

    async def async_month_tasks(self, sn: str) -> "tuple[MonthTask, ...]":
        from .month_tasks import rows

        return await self._run(lambda: rows(self._db(), vehicle_key(sn)))

    async def async_schedule_months(
        self,
        sn: str,
        months: tuple[str, ...],
        now: datetime,
        *,
        guard: Callable[[], bool],
        source: str = "manual",
    ) -> None:
        from .month_tasks import MonthTaskCapacity, schedule

        utc_string(now)

        def commit() -> None:
            try:
                schedule(self._db(write=True), vehicle_key(sn), months, now, source=source)
            except MonthTaskCapacity as err:
                raise ArchiveError(ArchiveFailure.BUSY) from err

        await self._run(commit, guard=guard)

    async def async_claim_month(
        self,
        sn: str,
        now: datetime,
        budget: int,
        *,
        guard: Callable[[], bool],
        months: tuple[str, ...] | None = None,
    ) -> "MonthTask | None":
        from .month_tasks import claim

        utc_string(now)
        if type(budget) is not int or not 1 <= budget <= 60:
            raise ValueError("Invalid month budget")

        def commit() -> "MonthTask | None":
            db = self._db(write=True)
            vehicle = vehicle_key(sn)
            return claim(
                db,
                vehicle,
                now,
                lambda: self._reserve_background(db, vehicle, now.timestamp(), budget, "sparse"),
                months,
            )

        return await self._run(commit, guard=guard)

    async def async_finish_month(
        self,
        sn: str,
        expected: "MonthTask",
        now: datetime,
        *,
        guard: Callable[[], bool],
        success: bool,
        complete: bool | None = None,
        reason: str | None = None,
        delay: float = 0,
    ) -> bool:
        from .month_tasks import finish

        utc_string(now)
        return await self._run(
            lambda: finish(
                self._db(write=True),
                vehicle_key(sn),
                expected,
                now,
                success=success,
                complete=complete,
                reason=reason,
                delay=delay,
            ),
            guard=guard,
        )

    async def async_cancel_months(
        self, sn: str, months: tuple[str, ...], *, guard: Callable[[], bool]
    ) -> None:
        from .month_tasks import cancel

        for month in months:
            previous_month(month)
        await self._run(lambda: cancel(self._db(write=True), vehicle_key(sn), months), guard=guard)

    async def async_preservation_claim(
        self, sn: str, now: datetime, budget: int, *, guard: Callable[[], bool]
    ) -> "PreservationTask | None":
        utc_string(now)
        if type(budget) is not int or not 1 <= budget <= 60:
            raise ValueError("Invalid preservation budget")
        return await self._run(
            partial(self._preservation_claim, vehicle_key(sn), now, budget), guard=guard
        )

    def _preservation_claim(
        self, vehicle: str, now: datetime, budget: int
    ) -> "PreservationTask | None":
        from .detail_followup import put as put_followup
        from .preservation import PreservationTask, fingerprint, mutable_detail, task_state

        epoch = now.timestamp()
        db = self._db(write=True)
        with db:
            if self._history_paused(vehicle):
                return None
            limit = db.execute("SELECT value FROM meta WHERE key='preservation_budget'").fetchone()
            if limit is not None and float(limit[0]) > epoch:
                return None
            rows = db.execute(
                "SELECT r.id,r.data,r.revision,m.value,h.value FROM rides r LEFT JOIN meta m "
                "ON m.key='preservation:'||r.vehicle||':'||r.id LEFT JOIN meta h "
                "ON h.key='preservation_detail:'||r.vehicle||':'||r.id "
                "WHERE r.vehicle=:vehicle AND (m.value IS NULL OR "
                "json_extract(m.value,'$.revision')!=r.revision OR "
                "json_extract(m.value,'$.next_attempt')<=:epoch OR "
                "(h.value IS NULL AND "
                "(r.ended>=:recent OR (r.ended IS NULL AND r.started>=:recent)))) "
                # Oldest confirmed end first within the existing local risk band;
                # do not infer a deadline for missing ends or change other ordering.
                "ORDER BY CASE WHEN r.ended>=:risk_start AND r.ended<=:risk_end "
                "THEN 0 ELSE 1 END, "
                "CASE WHEN r.ended>=:risk_start AND r.ended<=:risk_end THEN r.ended END, "
                "r.started DESC,r.id LIMIT 128",
                {
                    "vehicle": vehicle,
                    "epoch": epoch,
                    "recent": utc_string(now - timedelta(days=1)),
                    "risk_start": utc_string(now - timedelta(days=180)),
                    "risk_end": utc_string(now - timedelta(days=150)),
                },
            ).fetchall()
            for identity, data, revision, value, content_marker in rows:
                ride = restored_ride(decoded(data))
                if ride.detail_id is None:
                    put_followup(db, vehicle, identity, None)
                    ignored = {
                        "fingerprint": fingerprint(ride),
                        "attempts": 0,
                        "observations": 2,
                        "next_attempt": 4102444800,
                        "reason": None,
                        "revision": revision,
                    }
                    db.execute(
                        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) "
                        "DO UPDATE SET value=excluded.value",
                        (f"preservation:{vehicle}:{identity}", encoded(ignored)),
                    )
                    continue
                key = f"preservation:{vehicle}:{identity}"
                signature = fingerprint(ride)
                before = task_state(value) if value is not None else None
                state = (
                    before
                    if before and before["fingerprint"] == signature
                    else {
                        "fingerprint": signature,
                        "attempts": 0,
                        "observations": 0,
                        "next_attempt": 0,
                        "reason": None,
                        "revision": revision,
                    }
                )
                persisted = (
                    db.execute(
                        "SELECT latest_hash,observed FROM details WHERE vehicle=? AND ride=?",
                        (vehicle, identity),
                    ).fetchone()
                    if before is None
                    or (content_marker is None and before["fingerprint"] == signature)
                    else None
                )
                if persisted is not None:
                    # A saved recent response may still be an early/partial ride.
                    # Mature history stays reusable without another cloud request.
                    recent = mutable_detail(ride, now)
                    put_followup(db, vehicle, identity, persisted[0])
                    state["observations"] = 1 if recent else 2
                    state["next_attempt"] = (
                        stamp(persisted[1]).timestamp() + 600 if recent else 4102444800
                    )
                if content_marker is None and persisted is None:
                    put_followup(db, vehicle, identity, None)
                state["revision"] = revision
                if state["next_attempt"] > epoch:
                    db.execute(
                        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) "
                        "DO UPDATE SET value=excluded.value",
                        (key, encoded(state)),
                    )
                    continue
                interval = 3600 / budget
                state["attempts"] = min(255, state["attempts"] + 1)
                state["next_attempt"] = epoch + max(60, interval)
                db.execute(
                    "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value",
                    (key, encoded(state)),
                )
                self._reserve_background(db, vehicle, epoch, budget, "detail")
                return PreservationTask(ride, signature, state["attempts"], state["observations"])
        return None

    async def async_preservation_finish(
        self,
        sn: str,
        task: "PreservationTask",
        now: datetime,
        *,
        success: bool,
        revisit: bool = False,
        retry_after: float | None = None,
        reason: str = "unavailable",
        guard: Callable[[], bool],
    ) -> None:
        utc_string(now)
        await self._run(
            partial(
                self._preservation_finish,
                vehicle_key(sn),
                task,
                now,
                success,
                revisit,
                retry_after,
                reason,
            ),
            guard=guard,
        )

    def _preservation_finish(
        self,
        vehicle: str,
        task: "PreservationTask",
        now: datetime,
        success: bool,
        revisit: bool,
        retry_after: float | None,
        reason: str,
    ) -> None:
        from .detail_followup import get as get_followup
        from .detail_followup import put as put_followup
        from .preservation import fingerprint, retry_delay, task_state

        if task.ride.ride_id is None:
            raise ValueError("Missing preservation identity")
        key = f"preservation:{vehicle}:{task.ride.ride_id}"
        db = self._db(write=True)
        with db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            current = self._ride(vehicle, task.ride.ride_id)
            if row is None or current is None:
                return
            state = task_state(row[0])
            if state["fingerprint"] != task.fingerprint or state["attempts"] != task.attempts:
                return
            if success:
                signature = fingerprint(current[0])
                saved = db.execute(
                    "SELECT latest_hash FROM details WHERE vehicle=? AND ride=?",
                    (vehicle, task.ride.ride_id),
                ).fetchone()
                content_hash = saved[0] if saved else None
                unchanged = signature == task.fingerprint and content_hash == get_followup(
                    db, vehicle, task.ride.ride_id
                )
                state["fingerprint"] = signature
                put_followup(db, vehicle, task.ride.ride_id, content_hash)
                # Two successful downloads are not two equal observations.
                state["observations"] = min(2, state["observations"] + 1) if unchanged else 1
                state["attempts"] = 0
                state["reason"] = None
                state["next_attempt"] = (
                    now.timestamp() + 600 if revisit and state["observations"] < 2 else 4102444800
                )
            else:
                state["reason"] = reason
                state["next_attempt"] = min(
                    4102444800, now.timestamp() + retry_delay(task.attempts, retry_after)
                )
            state["revision"] = current[2]
            task_state(encoded(state))
            db.execute("UPDATE meta SET value=? WHERE key=?", (encoded(state), key))

    async def async_months(self, sn: str, months: tuple[str, ...]) -> tuple[ArchiveMonth, ...]:
        """One serialized read for at most six selected months plus the boundary."""
        if not 1 <= len(months) <= 7 or len(set(months)) != len(months):
            raise ValueError("Invalid archive month range")
        for month in months:
            previous_month(month)
        key = vehicle_key(sn)
        return await self._run(
            lambda: tuple(found for month in months if (found := self._month(key, month)))
        )

    async def async_statistics_months(
        self, sn: str, months: tuple[str, ...]
    ) -> tuple[ArchiveMonth, ...]:
        """Read retained partial memberships without changing upstream response semantics."""
        if not 1 <= len(months) <= 7 or len(set(months)) != len(months):
            raise ValueError("Invalid archive month range")
        for month in months:
            previous_month(month)
        return await self._run(partial(self._statistics_months, vehicle_key(sn), months))

    def _statistics_months(self, vehicle: str, months: tuple[str, ...]) -> tuple[ArchiveMonth, ...]:
        result = []
        count = size = 0
        for month in months:
            archived = self._month(vehicle, month)
            if archived is None:
                continue
            # A complete response supersedes omitted IDs. A partial omission
            # is not deletion; historical memberships remain eligible facts.
            assert archived.travel.summary is not None
            latest = " AND m.latest=1" if archived.travel.summary.list_complete is True else ""
            rides = []
            for data in self._db().execute(
                "SELECT r.data FROM rides r JOIN membership m "
                "ON m.vehicle=r.vehicle AND m.ride=r.id "
                f"WHERE m.vehicle=? AND m.month=?{latest}",
                (vehicle, month),
            ):
                count += 1
                size += len(data[0].encode())
                if count > MAX_TIMELINE_RIDES or size > MAX_TIMELINE_BYTES:
                    raise ArchiveError(ArchiveFailure.BUDGET)
                rides.append(replace(restored_ride(decoded(data[0])), query_month=month))
            result.append(replace(archived, statistics_rides=tuple(rides)))
        return tuple(result)

    async def async_projection(
        self, sn: str, months: tuple[str, ...], now: datetime
    ) -> ArchiveProjection:
        """No await between month selection, latest selection and revision capture."""
        if not 1 <= len(months) <= 7 or len(set(months)) != len(months):
            raise ValueError("Invalid projection month range")
        for month in months:
            previous_month(month)
        vehicle, as_of = vehicle_key(sn), utc_string(now)

        def read() -> ArchiveProjection:
            records = self._statistics_months(vehicle, months)
            limited = False
            try:
                latest = latest_query(self._db(), vehicle, as_of)
            except TimelineLimitError:
                latest, limited = None, True  # Never substitute an arbitrary older ride.
            return ArchiveProjection(records, latest, self._revision(), limited)

        return await self._run(read)

    async def async_detail_summary(
        self, sn: str, ride_id: str, month: str, *, guard: Callable[[], bool]
    ) -> Ride | None:
        """Resolve one retained membership, not only the latest upstream page."""
        previous_month(month)
        if not isinstance(ride_id, str) or opaque_id(ride_id) != ride_id:
            raise ValueError("Invalid ride identity")

        def read() -> Ride | None:
            row = (
                self._db()
                .execute(
                    "SELECT r.data FROM rides r JOIN membership m "
                    "ON m.vehicle=r.vehicle AND m.ride=r.id JOIN months mo "
                    "ON mo.vehicle=m.vehicle AND mo.month=m.month "
                    "WHERE r.vehicle=? AND r.id=? AND m.month=? "
                    "AND (m.latest=1 OR json_extract(mo.data,'$.summary.list_complete') IS NOT 1)",
                    (vehicle_key(sn), ride_id, month),
                )
                .fetchone()
            )
            return replace(restored_ride(decoded(row[0])), query_month=month) if row else None

        return await self._run(read, guard=guard)

    async def async_saved_detail_ids(self, sn: str, ids: tuple[str, ...]) -> frozenset[str]:
        """One bounded index lookup; never decompress detail payloads for a list."""
        if len(ids) > MAX_PAGE or any(
            not isinstance(value, str) or opaque_id(value) != value for value in ids
        ):
            raise ValueError("Invalid detail status page")
        if not ids:
            return frozenset()
        placeholders = ",".join("?" for _ in ids)

        def read() -> frozenset[str]:
            return frozenset(
                row[0]
                for row in self._db().execute(
                    f"SELECT ride FROM details WHERE vehicle=? AND ride IN ({placeholders})",
                    (vehicle_key(sn), *ids),
                )
            )

        return await self._run(read)

    async def async_month(self, sn: str, month: str) -> ArchiveMonth | None:
        previous_month(month)
        return await self._run(partial(self._month, vehicle_key(sn), month))

    def _month(self, vehicle: str, month: str) -> ArchiveMonth | None:
        row = (
            self._db()
            .execute(
                "SELECT data,observed,revision,backend,ids FROM months WHERE vehicle=? AND month=?",
                (vehicle, month),
            )
            .fetchone()
        )
        if row is None:
            return None
        ids = self._ids(row[4])
        # Fetch one bounded month in one SQL statement, not one statement per ride.
        indexed = {
            identity: restored_ride(decoded(data))
            for identity, data in self._db().execute(
                "SELECT r.id,r.data FROM rides r JOIN membership m "
                "ON m.vehicle=r.vehicle AND m.ride=r.id "
                "WHERE m.vehicle=? AND m.month=? AND m.latest=1",
                (vehicle, month),
            )
        }
        rides = tuple(
            replace(indexed[identity], query_month=month) for identity in ids if identity in indexed
        )
        found_ids = {ride.ride_id for ride in rides}
        total = (
            self._db()
            .execute(
                "SELECT COUNT(*) FROM membership WHERE vehicle=? AND month=?", (vehicle, month)
            )
            .fetchone()[0]
        )
        return ArchiveMonth(
            restored_month(decoded(row[0]), rides),
            stamp(row[1]),
            row[2],
            row[3],
            ids,
            tuple(identity for identity in ids if identity not in found_ids),
            total,
        )

    async def async_page(
        self,
        sn: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = MAX_PAGE,
        cursor: ArchiveCursor | None = None,
    ) -> ArchivePage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE or (start is None) != (end is None):
            raise ValueError("Invalid archive range")
        start_key, end_key = utc_string(start) if start else None, utc_string(end) if end else None
        if start_key is not None and end_key is not None and start_key >= end_key:
            raise ValueError("Invalid archive range")
        return await self._run(
            partial(self._page, vehicle_key(sn), start_key, end_key, limit, cursor)
        )

    async def async_timeline(
        self, sn: str, start: datetime, end: datetime, *, limit: int = MAX_TIMELINE_RIDES
    ) -> ArchiveTimeline:
        """One serialized, current-selection range read; never cloud backfill."""
        key = vehicle_key(sn)
        start_key, end_key = utc_string(start), utc_string(end)
        if (
            start_key >= end_key
            or (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds()
            > MAX_TIMELINE_DAYS * 86400
            or type(limit) is not int
            or not 1 <= limit <= MAX_TIMELINE_RIDES
        ):
            raise ValueError("Invalid archive timeline range")

        def query() -> ArchiveTimeline:
            try:
                return timeline_query(self._db(), key, start_key, end_key, limit)
            except TimelineLimitError:
                raise ArchiveError(ArchiveFailure.BUDGET) from None

        return await self._run(query)

    async def async_latest(self, sn: str, now: datetime) -> ArchivedRide | None:
        """Latest valid time-bounded observation, independent of query month."""
        key, now_key = vehicle_key(sn), utc_string(now)

        def query() -> ArchivedRide | None:
            try:
                return latest_query(self._db(), key, now_key)
            except TimelineLimitError:
                raise ArchiveError(ArchiveFailure.BUDGET) from None

        return await self._run(query)

    async def async_recorded_page(
        self,
        sn: str,
        start: datetime,
        end: datetime,
        now: datetime,
        *,
        limit: int = MAX_PAGE,
        cursor: RecordedCursor | None = None,
    ) -> RecordedPage:
        """Calendar selection as revision-fenced pages, without cloud or writes."""
        vehicle, start_key, end_key = vehicle_key(sn), utc_string(start), utc_string(end)
        as_of = cursor.as_of if cursor else utc_string(now)
        if (
            start_key >= end_key
            or (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds()
            > MAX_TIMELINE_DAYS * 86400
            or type(limit) is not int
            or not 1 <= limit <= MAX_PAGE
        ):
            raise ValueError("Invalid recorded range")
        zone = ZoneInfo(BUSINESS_TIMEZONE)
        first_month = start.astimezone(zone).strftime("%Y%m")
        last_month = (end - timedelta(microseconds=1)).astimezone(zone).strftime("%Y%m")

        def query() -> RecordedPage:
            try:
                return recorded_page(
                    self._db(),
                    self.owner,
                    vehicle,
                    start_key,
                    end_key,
                    as_of,
                    limit,
                    first_month,
                    last_month,
                    cursor,
                )
            except TimelineLimitError:
                raise ArchiveError(ArchiveFailure.BUDGET) from None
            except RecordedCursorMismatch:
                raise ArchiveError(ArchiveFailure.CURSOR) from None

        return await self._run(query)

    def _page(
        self,
        vehicle: str,
        start: str | None,
        end: str | None,
        limit: int,
        cursor: ArchiveCursor | None,
    ) -> ArchivePage:
        db = self._db()
        revision = self._revision()
        if cursor and (
            cursor.owner != self.owner
            or cursor.vehicle != vehicle
            or cursor.revision != revision
            or cursor.start != start
            or cursor.end != end
        ):
            raise ArchiveError(ArchiveFailure.CURSOR)
        where = ["vehicle=?"]
        arguments: list[str | None] = [vehicle]
        if start is not None:
            where += ["ended>?", "started<?", "started<>''", "ended>started"]
            arguments += [start, end]
        if cursor:
            where += ["(started,id)>(?,?)"]
            arguments += [cursor.after_start, cursor.after_id]
        rows = db.execute(
            f"SELECT data,started,id FROM rides WHERE {' AND '.join(where)} "
            "ORDER BY started,id LIMIT ?",
            (*arguments, limit + 1),
        ).fetchall()
        selected = rows[:limit]
        following = (
            ArchiveCursor(
                self.owner, vehicle, revision, start, end, selected[-1][1], selected[-1][2]
            )
            if len(rows) > limit
            else None
        )
        return ArchivePage(
            tuple(restored_ride(decoded(row[0])) for row in selected), revision, following
        )

    async def async_import_legacy(
        self,
        months: dict[str, dict[str, StoredMonth]],
        rides: dict[str, dict[str, StoredRide]],
    ) -> bool:
        # Immutable rows plus detached mappings; the source Store remains free
        # to update its compatibility view while the worker imports this snapshot.
        month_copy = {key: dict(rows) for key, rows in months.items()}
        ride_copy = {key: dict(rows) for key, rows in rides.items()}
        return await self._run(partial(self._import_legacy, month_copy, ride_copy))

    def _import_legacy(
        self,
        months: dict[str, dict[str, StoredMonth]],
        rides: dict[str, dict[str, StoredRide]],
    ) -> bool:
        db = self._db(write=True)
        if db.execute("SELECT 1 FROM meta WHERE key='legacy_import_sha'").fetchone():
            return False
        fingerprint, month_rows, ride_rows = prepare_legacy(months, rides)
        prepared = []
        for vehicle, row, travel in month_rows:
            ids = encoded({"ids": row.returned_ids})
            self._ids(ids)
            if (
                row.backend_version is not None
                and version_metadata(row.backend_version) != row.backend_version
            ):
                raise ValueError("Invalid legacy backend version")
            if type(row.revision) is not int or row.revision < 1:
                raise ValueError("Invalid legacy revision")
            prepared.append((vehicle, row, encoded(month_data(travel)), ids))
        with db:
            for vehicle, ride_row, ride in ride_rows:
                observed = utc_string(stamp(ride_row.received_at))
                db.execute(
                    "INSERT OR IGNORE INTO vehicles VALUES (?,NULL,?,0)", (vehicle, observed)
                )
                self._upsert_ride(vehicle, ride, observed)
            for vehicle, row, data, ids in prepared:
                observed = utc_string(stamp(row.received_at))
                db.execute(
                    "INSERT OR IGNORE INTO vehicles VALUES (?,NULL,?,0)", (vehicle, observed)
                )
                before = db.execute(
                    "SELECT observed FROM months WHERE vehicle=? AND month=?", (vehicle, row.month)
                ).fetchone()
                if before and before[0] >= observed:
                    continue
                db.execute(
                    "INSERT INTO months VALUES (?,?,?,?,?,?,?) ON "
                    "CONFLICT(vehicle,month) DO UPDATE SET "
                    "data=excluded.data,observed=excluded.observed,revision=excluded.revision,backend=excluded.backend,ids=excluded.ids",
                    (vehicle, row.month, data, observed, row.revision, row.backend_version, ids),
                )
                for identity in row.returned_ids:
                    if self._ride(vehicle, identity) is not None:
                        db.execute(
                            "INSERT OR IGNORE INTO membership VALUES (?,?,?,1)",
                            (vehicle, row.month, identity),
                        )
            db.execute("INSERT INTO meta VALUES ('legacy_import_sha',?)", (fingerprint,))
            if prepared or ride_rows:
                self._revision(bump=True)
        return True

    def _sync_job(self) -> SyncJob | None:
        row = self._db().execute("SELECT value FROM meta WHERE key='sync_job'").fetchone()
        return restored_job(decoded(row[0])) if row else None

    def _put_sync(self, job: SyncJob) -> None:
        data = encoded(job_data(job))
        restored_job(decoded(data))
        self._db(write=True).execute(
            "INSERT INTO meta VALUES ('sync_job',?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value",
            (data,),
        )

    async def async_sync_job(self) -> SyncJob | None:
        return await self._run(self._sync_job)

    @property
    def background_idle(self) -> bool:
        """Admission hint only; the serialized actor and guards remain authoritative."""
        return (
            self._opened
            and not self._closing
            and not self.write_paused
            and not self.backup_paused
            and self._pending == 0
        )

    async def async_reconstruction_order(self, vehicles: tuple[str, ...]) -> tuple[str, ...]:
        def read() -> tuple[str, ...]:
            row = (
                self._db()
                .execute("SELECT value FROM meta WHERE key='reconstruction_vehicle'")
                .fetchone()
            )
            ordered = sorted(set(vehicles), key=vehicle_key)
            if row:
                ordered = [sn for sn in ordered if vehicle_key(sn) > row[0]] + [
                    sn for sn in ordered if vehicle_key(sn) <= row[0]
                ]
            return tuple(ordered)

        return await self._run(read)

    async def async_audit_order(self, vehicles: tuple[str, ...]) -> tuple[str, ...]:
        def read() -> tuple[str, ...]:
            row = (
                self._db()
                .execute("SELECT value FROM meta WHERE key='history_audit_vehicle'")
                .fetchone()
            )
            ordered = sorted(set(vehicles), key=vehicle_key)
            if row:
                ordered = [sn for sn in ordered if vehicle_key(sn) > row[0]] + [
                    sn for sn in ordered if vehicle_key(sn) <= row[0]
                ]
            return tuple(ordered)

        return await self._run(read)

    async def async_background_history_audit(
        self, sn: str, now: datetime, *, guard: Callable[[], bool]
    ) -> AuditPlan | ObjectSummary | None:
        from .archive_audit import delta_step, get, start, step
        from .object_checks import CONTRACT
        from .payload_inventory import vehicle_revision

        utc_string(now)

        def advance() -> AuditPlan | ObjectSummary | None:
            db, vehicle = self._db(write=True), vehicle_key(sn)
            with db:
                if self._history_paused(vehicle):
                    return None
                plan = get(db, vehicle)
                # Finish a fixed captured range even if writes make its proof
                # stale. Never reset an active scan on every arriving cloud page.
                # Daily scrubbing also checks latent corruption of stable data.
                result: AuditPlan | ObjectSummary | None
                if (
                    plan is not None
                    and plan.complete
                    and (now - stamp(plan.updated_at)).total_seconds() < 86400
                ):
                    seen = db.execute(
                        "SELECT value FROM meta WHERE key=?", (f"history_delta_seen:{vehicle}",)
                    ).fetchone()
                    current = vehicle_revision(db, vehicle)
                    if seen and seen[0] == f"{current}:{CONTRACT}":
                        return None
                    result = delta_step(db, vehicle, now, 2)
                    if result.pending == 0:
                        db.execute(
                            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                            " DO UPDATE SET value=excluded.value",
                            (f"history_delta_seen:{vehicle}", f"{current}:{CONTRACT}"),
                        )
                else:
                    if plan is None or plan.complete:
                        start(db, vehicle, now, restart=True)
                    result = step(db, vehicle, now, 2)
                db.execute(
                    "INSERT INTO meta VALUES ('history_audit_vehicle',?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (vehicle,),
                )
                return result

        return await self._run(advance, guard=guard)

    async def async_object_checks(self, sn: str) -> ObjectSummary:
        from .object_checks import summary

        return await self._run(lambda: summary(self._db(), vehicle_key(sn)))

    async def async_history_audit(self, sn: str) -> AuditPlan | None:
        return (await self.async_history_audit_status(sn))[0]

    async def async_history_audit_status(self, sn: str) -> tuple[AuditPlan | None, int, int]:
        from .archive_audit import get
        from .payload_inventory import revision as inventory_revision

        def read() -> tuple[AuditPlan | None, int, int]:
            return (
                get(self._db(), vehicle_key(sn)),
                self._revision(),
                inventory_revision(self._db()),
            )

        return await self._run(read)

    async def async_history_verification_status(
        self, sn: str
    ) -> tuple[AuditPlan | None, int, int, ObjectSummary]:
        """One actor snapshot for fences and receipts; no await between its reads."""
        from .archive_audit import get
        from .object_checks import summary
        from .payload_inventory import revision as inventory_revision

        def read() -> tuple[AuditPlan | None, int, int, ObjectSummary]:
            db, vehicle = self._db(), vehicle_key(sn)
            return get(db, vehicle), self._revision(), inventory_revision(db), summary(db, vehicle)

        return await self._run(read)

    async def async_start_history_audit(
        self, sn: str, now: datetime, *, guard: Callable[[], bool], restart: bool = False
    ) -> AuditPlan:
        from .archive_audit import start

        utc_string(now)
        if type(restart) is not bool:
            raise ValueError("Invalid audit restart")

        def begin() -> AuditPlan:
            db = self._db(write=True)
            with db:
                return start(db, vehicle_key(sn), now, restart=restart)

        return await self._run(begin, guard=guard)

    async def async_step_history_audit(
        self, sn: str, now: datetime, *, guard: Callable[[], bool], limit: int = 8
    ) -> AuditPlan | None:
        from .archive_audit import step

        utc_string(now)
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("Invalid audit batch")

        def advance() -> AuditPlan | None:
            db = self._db(write=True)
            with db:
                return step(db, vehicle_key(sn), now, limit)

        return await self._run(advance, guard=guard)

    async def async_history_plan(self, sn: str) -> HistoryPlan | None:
        from .history_store import root

        return await self._run(lambda: root(self._db(), vehicle_key(sn)))

    def _history_paused(self, vehicle: str) -> bool:
        row = (
            self._db()
            .execute("SELECT value FROM meta WHERE key=?", (f"history_paused:{vehicle}",))
            .fetchone()
        )
        return bool(row and row[0] == "1")

    async def async_history_paused(self, sn: str) -> bool:
        return await self._run(lambda: self._history_paused(vehicle_key(sn)))

    async def async_schedule_history_run(
        self, sn: str, month: str, now: datetime, *, guard: Callable[[], bool]
    ) -> HistoryPlan:
        from .history_store import root
        from .incremental_runs import RunCapacity, schedule

        previous_month(month)
        utc_string(now)

        def commit() -> HistoryPlan:
            db = self._db(write=True)
            vehicle = vehicle_key(sn)
            with db:
                if root(db, vehicle) is None:
                    raise ArchiveError(ArchiveFailure.OWNER)
                try:
                    return schedule(db, vehicle, month, now)
                except RunCapacity:
                    raise ArchiveError(ArchiveFailure.BUSY) from None

        return await self._run(commit, guard=guard)

    async def async_history_run(self, sn: str, identity: str) -> HistoryPlan | None:
        from .incremental_runs import get

        return await self._run(lambda: get(self._db(), vehicle_key(sn), identity))

    async def async_history_family_turn(self) -> int:
        def read() -> int:
            row = (
                self._db()
                .execute("SELECT value FROM meta WHERE key='history_family_turn'")
                .fetchone()
            )
            return int(row[0]) if row else 0

        return await self._run(read)

    async def async_set_history_family_turn(self, turn: int) -> None:
        if type(turn) is not int or not 0 <= turn < 3:
            raise ValueError("Invalid history family turn")

        def save() -> None:
            db = self._db(write=True)
            with db:
                db.execute(
                    "INSERT INTO meta VALUES ('history_family_turn',?) ON CONFLICT(key)"
                    " DO UPDATE SET value=excluded.value",
                    (str(turn),),
                )

        await self._run(save)

    async def async_active_history_runs(self, sn: str) -> tuple[HistoryPlan, ...]:
        from .incremental_runs import active

        return await self._run(lambda: active(self._db(), vehicle_key(sn)))

    async def async_reconcile_history_month(
        self,
        sn: str,
        month: str,
        now: datetime,
        signature: str,
        complete: bool,
        *,
        guard: Callable[[], bool],
    ) -> HistoryPlan | None:
        from .history_plan import HistoryState
        from .history_store import root
        from .incremental_runs import RunCapacity, get, put, schedule

        previous_month(month)
        if (
            len(signature) != 64
            or any(c not in "0123456789abcdef" for c in signature)
            or type(complete) is not bool
        ):
            raise ValueError("Invalid incremental reconciliation")

        def reconcile() -> HistoryPlan | None:
            db = self._db(write=True)
            vehicle = vehicle_key(sn)
            marker = f"history_incremental_seen:{vehicle}:{month}"
            with db:
                if root(db, vehicle) is None:
                    raise ArchiveError(ArchiveFailure.OWNER)
                previous = db.execute("SELECT value FROM meta WHERE key=?", (marker,)).fetchone()
                active = db.execute(
                    "SELECT value FROM meta WHERE key=?", (f"history_run_active:{vehicle}:{month}",)
                ).fetchone()
                old = get(db, vehicle, active[0]) if active else None
                if complete:
                    if (
                        old is not None
                        and old.state in {HistoryState.DISCOVER, HistoryState.WAITING}
                        and old.next_page == 1
                    ):
                        put(
                            db,
                            replace(
                                old,
                                state=HistoryState.RECONCILED,
                                reason=None,
                                retry_at=None,
                                updated_at=utc_string(now),
                                revision=old.revision + 1,
                            ),
                        )
                    db.execute(
                        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                        " DO UPDATE SET value=excluded.value",
                        (marker, signature),
                    )
                    return None
                if previous and previous[0] == signature:
                    return old
                try:
                    plan = schedule(db, vehicle, month, now)
                except RunCapacity:
                    raise ArchiveError(ArchiveFailure.BUSY) from None
                token_key = f"history_run_signature:{vehicle}:{plan.plan_id}"
                token = db.execute("SELECT value FROM meta WHERE key=?", (token_key,)).fetchone()
                if token is None:
                    db.execute("INSERT INTO meta VALUES (?,?)", (token_key, signature))
                    token = (signature,)
                if token[0] == signature:
                    db.execute(
                        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                        " DO UPDATE SET value=excluded.value",
                        (marker, signature),
                    )
                return plan

        return await self._run(reconcile, guard=guard)

    async def async_history_requested(self, sn: str) -> bool:
        def read() -> bool:
            found = (
                self._db()
                .execute(
                    "SELECT value FROM meta WHERE key=?", (f"history_requested:{vehicle_key(sn)}",)
                )
                .fetchone()
            )
            return found is not None and found[0] == "1"

        return await self._run(read)

    async def async_request_history(self, sn: str, *, guard: Callable[[], bool]) -> None:
        def save() -> None:
            db = self._db(write=True)
            with db:
                db.execute(
                    "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                    " DO UPDATE SET value=excluded.value",
                    (f"history_requested:{vehicle_key(sn)}", "1"),
                )

        await self._run(save, guard=guard)

    async def async_set_history_paused(
        self, sn: str, paused: bool, *, guard: Callable[[], bool]
    ) -> None:
        if type(paused) is not bool:
            raise ValueError("Invalid history pause value")

        def save() -> None:
            db = self._db(write=True)
            with db:
                db.execute(
                    "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                    " DO UPDATE SET value=excluded.value",
                    (f"history_paused:{vehicle_key(sn)}", "1" if paused else "0"),
                )

        await self._run(save, guard=guard)

    async def async_history_evidence(
        self, sn: str, *, guard: Callable[[], bool]
    ) -> HistoryEvidence:
        """Read metadata/counts together on the actor; never decompress payloads."""
        from .detail_health import summary
        from .history_store import root
        from .incremental_runs import active

        def read() -> HistoryEvidence:
            db = self._db()
            vehicle = vehicle_key(sn)
            plan = root(db, vehicle)
            rows = dict(
                db.execute(
                    "SELECT key,value FROM meta WHERE key IN (?,?,?,?,?)",
                    (
                        f"history_last_success:{vehicle}",
                        f"history_last_commit:{vehicle}",
                        "preservation_budget",
                        f"history_paused:{vehicle}",
                        f"history_requested:{vehicle}",
                    ),
                )
            )
            success = rows.get(f"history_last_success:{vehicle}")
            committed = rows.get(f"history_last_commit:{vehicle}")
            successes = [stamp(value) for value in (success, committed) if value]
            budget = rows.get("preservation_budget")
            return HistoryEvidence(
                plan,
                summary(db, vehicle, plan.plan_id) if plan else None,
                active(db, vehicle),
                max(successes) if successes else None,
                datetime.fromtimestamp(float(budget), UTC) if budget else None,
                rows.get(f"history_paused:{vehicle}") == "1",
                rows.get(f"history_requested:{vehicle}") == "1",
                stamp(success) if success else None,
                db.execute(
                    "SELECT count(DISTINCT month) FROM history_months WHERE vehicle=?"
                    " AND verified=1 AND pages>1 AND total=known",
                    (vehicle,),
                ).fetchone()[0],
            )

        return await self._run(read, guard=guard)

    async def async_history_health(self, sn: str, plan_id: str) -> HistoryHealth:
        from .detail_health import summary
        from .history_store import resolve

        def read() -> HistoryHealth:
            db = self._db()
            vehicle = vehicle_key(sn)
            current = resolve(db, vehicle, plan_id)
            if current is None or current.plan_id != plan_id:
                raise ArchiveError(ArchiveFailure.OWNER)
            return summary(db, vehicle, plan_id)

        return await self._run(read)

    async def async_backfill_detail_health(
        self, sn: str, plan_id: str, *, guard: Callable[[], bool], limit: int = 16
    ) -> int:
        from .detail_health import put as put_health
        from .history_store import resolve

        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("Invalid detail health batch")

        def backfill() -> int:
            db = self._db(write=True)
            vehicle = vehicle_key(sn)
            current = resolve(db, vehicle, plan_id)
            if current is None or current.plan_id != plan_id:
                raise ArchiveError(ArchiveFailure.OWNER)
            rows = db.execute(
                "SELECT DISTINCT i.ride FROM history_ids i JOIN details d"
                " ON d.vehicle=i.vehicle AND d.ride=i.ride LEFT JOIN meta m"
                " ON m.key='detail_health:'||i.vehicle||':'||i.ride WHERE i.vehicle=? AND i.plan=?"
                " AND (m.value IS NULL OR json_extract(m.value,'$.latest_hash')!=d.latest_hash"
                " OR json_extract(m.value,'$.geometry_hash')!=d.geometry_hash)"
                " ORDER BY i.ride LIMIT ?",
                (vehicle, plan_id, limit),
            ).fetchall()
            with db:
                for (identity,) in rows:
                    detail = self._private_detail(vehicle, identity, 2000)
                    if detail is None:
                        raise ValueError("Missing detail health source")
                    assert detail.latest is not None  # Strict source required for quality indexing.
                    latest = parse_ride(
                        detail.latest.payload(),
                        detail.latest.query_month or detail.ride.query_month,
                        source=Endpoint.TRIP_DETAIL,
                        max_points=2000,
                    )
                    put_health(
                        db,
                        vehicle,
                        identity,
                        detail.latest_hash,
                        detail.geometry_hash,
                        latest.track_quality,
                        detail.ride.track_quality,
                    )
            return len(rows)

        return await self._run(backfill, guard=guard)

    async def async_finish_history_baseline(
        self, sn: str, expected: HistoryPlan, now: datetime, *, guard: Callable[[], bool]
    ) -> HistoryPlan:
        from .detail_health import summary
        from .history_plan import HistoryState
        from .history_store import HistoryConflict, put_root, resolve

        if expected.vehicle != vehicle_key(sn):
            raise ValueError("Invalid baseline scope")

        def finish() -> HistoryPlan:
            db = self._db(write=True)
            with db:
                if resolve(db, expected.vehicle, expected.plan_id) != expected:
                    raise ArchiveError(ArchiveFailure.BUSY)
                if not expected.discovery_complete or expected.state not in {
                    HistoryState.PRESERVE,
                    HistoryState.VERIFY,
                }:
                    return expected
                health = summary(db, expected.vehicle, expected.plan_id)
                if (
                    health.missing
                    or health.unmapped
                    or health.quality_pending
                    or health.reference_issues
                ):
                    return expected
                result = replace(
                    expected,
                    state=HistoryState.WITH_GAPS
                    if expected.gap_months or health.archive_has_gaps
                    else HistoryState.COMPLETE,
                    revision=expected.revision + 1,
                    updated_at=utc_string(now),
                )
                try:
                    put_root(db, result, expected)
                except HistoryConflict:
                    raise ArchiveError(ArchiveFailure.BUSY) from None
                db.execute(
                    "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                    " DO UPDATE SET value=excluded.value",
                    (f"history_last_success:{expected.vehicle}", utc_string(now)),
                )
                return result

        return await self._run(finish, guard=guard)

    async def async_set_history_plan(
        self,
        sn: str,
        plan: HistoryPlan,
        expected: HistoryPlan | None,
        *,
        guard: Callable[[], bool],
    ) -> None:
        from .history_store import HistoryConflict, put_root

        if plan.vehicle != vehicle_key(sn):
            raise ValueError("Invalid history plan scope")

        def commit() -> None:
            db = self._db(write=True)
            try:
                with db:
                    db.execute(
                        "INSERT OR IGNORE INTO vehicles VALUES (?,NULL,?,0)",
                        (plan.vehicle, plan.updated_at),
                    )
                    put_root(db, plan, expected)
            except HistoryConflict:
                raise ArchiveError(ArchiveFailure.BUSY) from None

        await self._run(commit, guard=guard)

    async def async_reuse_history_month(
        self, sn: str, now: datetime, *, guard: Callable[[], bool]
    ) -> bool:
        from .history_reuse import reuse

        utc_string(now)

        def commit() -> bool:
            db = self._db(write=True)
            with db:
                if self._history_paused(vehicle_key(sn)):
                    return False
                return reuse(db, vehicle_key(sn), now)

        return await self._run(commit, guard=guard)

    async def async_claim_history_page(
        self,
        sn: str,
        now: datetime,
        budget: int,
        *,
        guard: Callable[[], bool],
        plan_id: str | None = None,
    ) -> HistoryPlan | None:
        from .history_plan import HistoryState
        from .history_store import put_root, resolve, root

        observed = utc_string(now)
        if type(budget) is not int or not 1 <= budget <= 60:
            raise ValueError("Invalid history budget")

        def claim() -> HistoryPlan | None:
            db = self._db(write=True)
            vehicle = vehicle_key(sn)
            with db:
                current = (
                    resolve(db, vehicle, plan_id) if plan_id is not None else root(db, vehicle)
                )
                if (
                    current is None
                    or current.next_month is None
                    or current.state
                    not in {HistoryState.DISCOVER, HistoryState.QUERYING, HistoryState.WAITING}
                    or current.retry_at is not None
                    and stamp(current.retry_at) > now
                ):
                    return None
                if not self._reserve_background(db, vehicle, now.timestamp(), budget, "month"):
                    return None
                claimed = replace(
                    current,
                    state=HistoryState.QUERYING,
                    reason=None,
                    updated_at=observed,
                    revision=current.revision + 1,
                    attempts=min(255, current.attempts + 1),
                    retry_at=utc_string(now + timedelta(seconds=120)),
                )
                put_root(db, claimed, current)
                return claimed

        return await self._run(claim, guard=guard)

    async def async_wait_history_page(
        self,
        expected: HistoryPlan,
        now: datetime,
        reason: str,
        delay: float,
        *,
        guard: Callable[[], bool],
    ) -> None:
        from .history_plan import HistoryState
        from .history_store import HistoryConflict, put_root

        if not isinstance(delay, (int, float)) or not 0 <= delay <= 86400:
            raise ValueError("Invalid history retry delay")
        following = replace(
            expected,
            state=HistoryState.WAITING,
            reason=reason,
            revision=expected.revision + 1,
            updated_at=utc_string(now),
            retry_at=utc_string(now + timedelta(seconds=delay)),
        )

        def wait() -> None:
            db = self._db(write=True)
            try:
                with db:
                    put_root(db, following, expected)
            except HistoryConflict:
                raise ArchiveError(ArchiveFailure.BUSY) from None

        await self._run(wait, guard=guard)

    async def async_accept_history_page(
        self,
        sn: str,
        expected: HistoryPlan,
        raw: RawRecord,
        *,
        guard: Callable[[], bool],
    ) -> PageReceipt:
        from .history_store import HistoryConflict, accept_page

        if expected.vehicle != vehicle_key(sn):
            raise ValueError("Invalid history page scope")

        def commit() -> PageReceipt:
            db = self._db(write=True)
            try:
                with db:
                    if self._history_paused(expected.vehicle):
                        raise ArchiveError(ArchiveFailure.BUSY)
                    receipt = accept_page(db, expected, raw)
                    if receipt.stop is None:
                        self._history_committed(expected.vehicle, utc_string(raw.received_at))
                    from .payload_inventory import advance as advance_inventory

                    advance_inventory(db, expected.vehicle)
                    if receipt.stop != "schema":
                        from .adapters import travel as parse_month

                        assert raw.query_month is not None
                        normalized = parse_month(raw.payload(), raw.query_month)
                        observed = utc_string(raw.received_at)
                        latest = db.execute(
                            "SELECT observed FROM months WHERE vehicle=? AND month=?",
                            (expected.vehicle, raw.query_month),
                        ).fetchone()
                        if latest and latest[0] > observed:
                            changed = False
                            for ride in normalized.rides:
                                restored_ride(decoded(encoded(ride_data(ride))))
                                if ride.ride_id is None:
                                    continue
                                changed = (
                                    self._upsert_ride(expected.vehicle, ride, observed, True)
                                    or changed
                                )
                                db.execute(
                                    "INSERT OR IGNORE INTO membership VALUES (?,?,?,0)",
                                    (expected.vehicle, raw.query_month, ride.ride_id),
                                )
                            if changed:
                                self._revision(bump=True)
                        else:
                            self._record_month(
                                expected.vehicle,
                                normalized,
                                observed,
                                raw.backend_version,
                                True,
                                sync_checkpoint=False,
                            )
                return receipt
            except HistoryConflict:
                raise ArchiveError(ArchiveFailure.BUSY) from None

        return await self._run(commit, guard=guard)

    async def async_history_page(
        self,
        sn: str,
        plan_id: str,
        month: str,
        number: int,
        *,
        guard: Callable[[], bool],
    ) -> RawRecord | None:
        from .history_plan import MAX_PAGES
        from .history_store import page

        previous_month(month)
        if (
            type(number) is not int
            or not 1 <= number <= MAX_PAGES
            or len(plan_id) != 32
            or any(c not in "0123456789abcdef" for c in plan_id)
        ):
            raise ValueError("Invalid history page query")
        return await self._run(
            lambda: page(self._db(), vehicle_key(sn), plan_id, month, number), guard=guard
        )

    async def async_set_sync_job(
        self,
        job: SyncJob,
        expected: SyncJob | None,
        *,
        guard: Callable[[], bool],
    ) -> None:
        def commit() -> None:
            db = self._db(write=True)
            with db:
                current = self._sync_job()
                if current != expected or (
                    expected is not None
                    and (job.job_id != expected.job_id or job.revision != expected.revision + 1)
                    and not expected.terminal
                ):
                    raise ArchiveError(ArchiveFailure.BUSY)
                self._put_sync(job)

        await self._run(commit, guard=guard)

    async def async_skip_sync_month(
        self,
        expected: SyncJob,
        received: datetime,
        *,
        guard: Callable[[], bool],
    ) -> SyncJob:
        def skip() -> SyncJob:
            db = self._db(write=True)
            with db:
                if self._sync_job() != expected or expected.next_month is None:
                    raise ArchiveError(ArchiveFailure.BUSY)
                month = self._month(expected.vehicle, expected.next_month)
                if month is None or month.travel.summary is None:
                    raise ArchiveError(ArchiveFailure.SCHEMA)
                advanced = expected.advance(
                    utc_string(received), queried=False, complete=month.travel.summary.list_complete
                )
                self._put_sync(advanced)
                return advanced

        return await self._run(skip, guard=guard)

    async def async_backup(self, destination: Path) -> None:
        await self._run(partial(self._backup, destination))

    def _backup(self, destination: Path, *, recovery: bool = False) -> None:
        # SQLite backup on its own uncommitted writer can wait indefinitely.
        # Never commit someone else's transaction or silently roll it back.
        if self._db().in_transaction:
            raise ArchiveError(ArchiveFailure.BUSY)
        # Never overwrite an existing user backup, and never copy a live file
        # with its transaction journal omitted.
        if destination == self.path or destination.exists() or destination.is_symlink():
            raise ArchiveError(ArchiveFailure.STORAGE)
        descriptor, filename = tempfile.mkstemp(prefix=".ninebot-backup-", dir=destination.parent)
        temporary = Path(filename)
        os.close(descriptor)
        try:
            target = sqlite3.connect(temporary)
            try:
                self._db().backup(target)
                # A recovery copy preserves damaged derived time indexes;
                # source/schema/owner checks still apply. Ordinary backup and
                # startup remain strict; this is not a certified restore image.
                self._check(target, allow_time_index_drift=recovery)
            finally:
                target.close()
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            # Exclusive publication: unlike replace(), a concurrently created
            # destination cannot be overwritten. The visible backup is complete.
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    async def async_close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            self._close_task.exception()
            raise asyncio.CancelledError
        self._close_task.result()

    async def _close(self) -> None:
        def finish() -> None:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

        try:
            await self._run(finish, lifecycle=True)
        finally:
            # The worker queue has drained; wait=False cannot block HA's loop.
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._opened = False
