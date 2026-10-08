"""Account-scoped SQLite ride archive, serialized entirely outside HA's loop.

Only reviewed normalized scalar facts persist. The bounded RawStore remains
responsible for raw responses and GPS. Historical membership is retained even
when a subsequent incomplete month response omits previously observed rides.
"""

import asyncio
import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as WorkerTimeout
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TypeVar

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
    MAX_TIMELINE_DAYS,
    MAX_TIMELINE_RIDES,
    ArchivedRide,
    ArchiveTimeline,
    TimelineLimitError,
    latest_query,
    timeline_query,
)
from .models import TravelMonth, VehicleProfile
from .parsing import previous_month
from .raw import version_metadata
from .ride_models import Ride
from .statistics_store import StoredMonth, StoredRide
from .sync_job import SyncJob, SyncState, job_data, restored_job
from .travel import MAX_RIDES, opaque_id

SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
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


class RideArchive:
    """One connection and writer thread per entry; bounded coroutine admission."""

    def __init__(self, path: Path, owner: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if len(owner) != 64 or any(char not in "0123456789abcdef" for char in owner):
            raise ValueError("Invalid archive owner scope")
        if type(max_bytes) is not int or not 32768 <= max_bytes <= DEFAULT_MAX_BYTES:
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

    def _check(self, db: sqlite3.Connection) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ArchiveError(ArchiveFailure.SCHEMA)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != TABLE_COLUMNS.keys():
            raise ArchiveError(ArchiveFailure.SCHEMA)
        for table, columns in TABLE_COLUMNS.items():
            if tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})")) != columns:
                raise ArchiveError(ArchiveFailure.SCHEMA)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        if meta.get("owner") != self.owner:
            raise ArchiveError(ArchiveFailure.OWNER)
        if not str(meta.get("revision", "")).isdigit():
            raise ArchiveError(ArchiveFailure.SCHEMA)
        if "sync_job" in meta:
            restored_job(decoded(meta["sync_job"]))
        if (
            db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchone()
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
                or started != (utc_string(ride.started_at) if ride.started_at else "")
                or ended != (utc_string(ride.ended_at) if ride.ended_at else None)
            ):
                raise ValueError("Invalid archived ride index")
            stamp(observed)

    def _open(self) -> None:
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ArchiveError(ArchiveFailure.STORAGE)
        if self.path.exists():
            probe = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            try:
                self._check(probe)
            finally:
                probe.close()
        else:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            db = sqlite3.connect(self.path)
            try:
                with db:
                    for sql in SCHEMA:
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
        self, vehicle: str, travel: TravelMonth, observed: str, backend: str | None, ordered: bool
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
        with db:
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
                job is not None
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
    ) -> bool:
        if type(ordered) is not bool or (ordered and guard is None):
            raise ValueError("Ordered receipts require an ownership/revision fence")
        return await self._run(
            partial(self._record_detail, vehicle_key(sn), ride, utc_string(received), ordered),
            guard=guard,
        )

    def _record_detail(self, vehicle: str, ride: Ride, observed: str, ordered: bool) -> bool:
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
        with db:
            changed = self._upsert_ride(vehicle, ride, observed, ordered)
            if changed:
                self._revision(bump=True)
        return changed

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

    def _backup(self, destination: Path) -> None:
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
                self._check(target)
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
