"""Current per-month raw source, separate from immutable history page evidence.

One bounded body per existing month, on the archive actor and transaction.
Replacing this current snapshot never removes historical page/detail originals.
Verification returns reviewed field names only, never raw data or ride IDs.
"""

import sqlite3
from dataclasses import dataclass, replace

from .archive_codec import decoded, encoded, month_data, restored_month, stamp
from .const import MAX_RESPONSE_BYTES
from .detail_codec import CorruptPayload, packed, unpacked
from .exceptions import NinebotError
from .history_plan import PROVIDERS
from .parsing import previous_month
from .raw import Endpoint, RawRecord, version_metadata

SCHEMA = (
    "CREATE TABLE month_sources (vehicle TEXT NOT NULL, month TEXT NOT NULL,"
    " digest TEXT NOT NULL, body BLOB NOT NULL, observed TEXT NOT NULL, backend TEXT,"
    " endpoint TEXT, parser INTEGER NOT NULL, PRIMARY KEY(vehicle,month),"
    " FOREIGN KEY(vehicle,month) REFERENCES months(vehicle,month))",
)
COLUMNS = {
    "month_sources": (
        "vehicle",
        "month",
        "digest",
        "body",
        "observed",
        "backend",
        "endpoint",
        "parser",
    )
}
MAX_COMPRESSED = MAX_RESPONSE_BYTES + 1024


@dataclass(frozen=True)
class MonthSourceCheck:
    month: str
    status: str
    received_at: str | None = None
    raw_payload_integrity_verified: bool = False
    projection_verified: bool = False
    conflicting_fields: tuple[str, ...] = ()
    scope: str = "current_month_summary_precision_and_ids"
    revision: int = 0


def put(
    db: sqlite3.Connection,
    vehicle: str,
    month: str,
    observed: str,
    backend: str | None,
    raw: RawRecord | None,
) -> None:
    if raw is None:
        return  # Old source remains historical/stale; never bind it to a new receipt.
    if (
        raw.endpoint is not Endpoint.TRAVEL
        or raw.query_month != month
        or raw.received_at != stamp(observed)
        or raw.backend_version != backend
        or raw.parser_contract_version != 1
    ):
        raise ValueError("Invalid current month source binding")
    digest, body, clean = packed(raw, source=Endpoint.TRAVEL)
    existing = db.execute(
        "SELECT digest,CASE WHEN length(body)<=? THEN body END FROM month_sources"
        " WHERE vehicle=? AND month=?",
        (MAX_COMPRESSED, vehicle, month),
    ).fetchone()
    if existing is not None and existing == (digest, body):
        db.execute(
            "UPDATE month_sources SET observed=?,backend=?,endpoint=?,parser=1"
            " WHERE vehicle=? AND month=?",
            (observed, backend, clean.endpoint_version, vehicle, month),
        )
    else:
        db.execute(
            "INSERT INTO month_sources VALUES (?,?,?,?,?,?,?,1)"
            " ON CONFLICT(vehicle,month) DO UPDATE SET digest=excluded.digest,body=excluded.body,"
            " observed=excluded.observed,backend=excluded.backend,endpoint=excluded.endpoint,"
            "parser=1",
            (vehicle, month, digest, body, observed, backend, clean.endpoint_version),
        )
    from .payload_inventory import advance

    advance(db, vehicle)


def check(db: sqlite3.Connection) -> None:
    # Structural validation only: bit rot is a per-source result, not a reason
    # to make every retained ride unavailable during startup/backup.
    for vehicle, month, digest, kind, size, observed, backend, endpoint, parser in db.execute(
        "SELECT vehicle,month,digest,typeof(body),length(body),observed,backend,endpoint,parser"
        " FROM month_sources"
    ):
        previous_month(month)
        stamp(observed)
        if (
            len(vehicle) != 64
            or any(c not in "0123456789abcdef" for c in vehicle)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or kind != "blob"
            or size < 0
            or parser != 1
            or any(
                value is not None and version_metadata(value) != value
                for value in (backend, endpoint)
            )
        ):
            raise ValueError("Invalid current month source metadata")


def inspect(db: sqlite3.Connection, vehicle: str, month: str) -> MonthSourceCheck:
    revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    return replace(_inspect(db, vehicle, month), revision=revision)


def _inspect(db: sqlite3.Connection, vehicle: str, month: str) -> MonthSourceCheck:
    from .adapters import travel

    parent = db.execute(
        "SELECT data,observed,backend,ids FROM months WHERE vehicle=? AND month=?", (vehicle, month)
    ).fetchone()
    row = db.execute(
        "SELECT digest,CASE WHEN length(body)<=? THEN body END,observed,backend,endpoint,parser"
        " FROM month_sources WHERE vehicle=? AND month=?",
        (MAX_COMPRESSED, vehicle, month),
    ).fetchone()
    if parent is None or row is None:
        return MonthSourceCheck(month, "missing")
    digest, body, observed, backend, endpoint, parser = row
    if stamp(observed) != stamp(parent[1]) or backend != parent[2]:
        return MonthSourceCheck(month, "stale", observed)
    try:
        raw = unpacked(
            body, digest, stamp(observed), month, backend, endpoint, source=Endpoint.TRAVEL
        )
    except CorruptPayload:
        return MonthSourceCheck(month, "corrupt", observed)
    if backend != "0.1.7" or endpoint not in (None, *PROVIDERS) or parser != 1:
        return MonthSourceCheck(month, "unsupported", observed, True)
    try:
        normalized = travel(raw.payload(), month)
        expected = decoded(encoded(month_data(normalized)))
        actual = decoded(parent[0])
        restored_month(actual)
        differences = tuple(
            f"summary.{key}"
            for key, value in expected["summary"].items()
            if actual["summary"].get(key) != value
        )
        if expected["precision"] != actual["precision"]:
            differences += ("precision",)
        if expected["month"] != actual["month"]:
            differences += ("month",)
        ids = list(
            dict.fromkeys(ride.ride_id for ride in normalized.rides if ride.ride_id is not None)
        )
        if decoded(parent[3]) != {"ids": ids}:
            differences += ("ids",)
    except (NinebotError, ValueError, KeyError, TypeError):
        return MonthSourceCheck(month, "unverifiable", observed, True)
    return MonthSourceCheck(
        month, "conflict" if differences else "match", observed, True, not differences, differences
    )
