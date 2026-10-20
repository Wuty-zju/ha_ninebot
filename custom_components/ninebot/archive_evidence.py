"""Superseded business originals inside the account database, not whole DB copies."""

import sqlite3

from .archive_codec import decoded, restored_ride, stamp
from .const import MAX_RESPONSE_BYTES
from .detail_codec import scoped_ride, unpacked
from .parsing import previous_month
from .raw import Endpoint, version_metadata

SCHEMA = (
    "CREATE TABLE archive_evidence (vehicle TEXT NOT NULL REFERENCES vehicles(vehicle),"
    " kind TEXT NOT NULL, scope TEXT NOT NULL, digest TEXT NOT NULL, body BLOB NOT NULL,"
    " observed TEXT NOT NULL, month TEXT NOT NULL, backend TEXT, endpoint TEXT,"
    " PRIMARY KEY(vehicle,kind,scope,digest))",
)
COLUMNS = {
    "archive_evidence": (
        "vehicle",
        "kind",
        "scope",
        "digest",
        "body",
        "observed",
        "month",
        "backend",
        "endpoint",
    )
}


def check(db: sqlite3.Connection, *, deep: bool = False) -> None:
    for (
        vehicle,
        kind,
        scope,
        digest,
        size,
        datatype,
        observed,
        month,
        backend,
        endpoint,
    ) in db.execute(
        "SELECT vehicle,kind,scope,digest,length(body),typeof(body),observed,month,backend,endpoint"
        " FROM archive_evidence"
    ):
        if (
            kind not in {"detail", "month", "page"}
            or not isinstance(scope, str)
            or not 1 <= len(scope) <= 512
            or any(ord(c) < 32 for c in scope)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or datatype != "blob"
            or not 0 < size <= MAX_RESPONSE_BYTES + 1024
            or any(v is not None and version_metadata(v) != v for v in (backend, endpoint))
        ):
            raise ValueError("Invalid preserved business evidence")
        previous_month(month)
        stamp(observed)
        if not deep:
            continue
        body = db.execute(
            "SELECT body FROM archive_evidence WHERE vehicle=? AND kind=? AND scope=? AND digest=?",
            (vehicle, kind, scope, digest),
        ).fetchone()[0]
        record = unpacked(
            body,
            digest,
            stamp(observed),
            month,
            backend,
            endpoint,
            source=Endpoint.TRIP_DETAIL if kind == "detail" else Endpoint.TRAVEL,
        )
        if kind == "detail":
            row = db.execute(
                "SELECT data FROM rides WHERE vehicle=? AND id=?", (vehicle, scope)
            ).fetchone()
            if row is None:
                raise ValueError("Missing preserved ride identity")
            scoped_ride(record, restored_ride(decoded(row[0])))


def absorb(db: sqlite3.Connection, source: sqlite3.Connection, vehicles: set[str]) -> None:
    """Keep every validated original not represented by the selected live roles."""
    rows = (
        "SELECT vehicle,'detail',ride,hash,body,observed,month,backend,endpoint"
        " FROM detail_payloads",
        "SELECT vehicle,'month',month,digest,body,observed,month,backend,endpoint"
        " FROM month_sources",
        "SELECT p.vehicle,'page',p.plan||':'||p.month||':'||p.page,p.digest,"
        "CASE WHEN length(p.body)>0 THEN p.body ELSE coalesce(h.body,"
        "(SELECT b.body FROM history_pages b WHERE b.digest=p.digest AND length(b.body)>0 LIMIT 1))"
        " END,p.observed,p.month,p.backend,p.endpoint FROM history_pages p"
        " LEFT JOIN history_payloads h ON h.digest=p.digest",
        "SELECT * FROM archive_evidence",
    )
    for sql in rows:
        for row in source.execute(sql):
            if row[0] in vehicles:
                vehicle, kind, scope, digest = row[:4]
                if kind == "detail":
                    present = db.execute(
                        "SELECT 1 FROM detail_payloads WHERE vehicle=? AND ride=? AND hash=?",
                        (vehicle, scope, digest),
                    ).fetchone()
                elif kind == "month":
                    present = db.execute(
                        "SELECT 1 FROM month_sources WHERE vehicle=? AND month=? AND digest=?",
                        (vehicle, scope, digest),
                    ).fetchone()
                else:
                    present = db.execute(
                        "SELECT 1 FROM history_pages WHERE vehicle=? AND"
                        " plan||':'||month||':'||page=? AND digest=?",
                        (vehicle, scope, digest),
                    ).fetchone()
                if present:
                    continue
                db.execute("INSERT OR IGNORE INTO archive_evidence VALUES (?,?,?,?,?,?,?,?,?)", row)


def prune_duplicates(db: sqlite3.Connection) -> None:
    db.execute(
        "DELETE FROM archive_evidence AS e WHERE"
        " (kind='detail' AND EXISTS (SELECT 1 FROM detail_payloads d WHERE"
        " d.vehicle=e.vehicle AND d.ride=e.scope AND d.hash=e.digest)) OR"
        " (kind='month' AND EXISTS (SELECT 1 FROM month_sources m WHERE"
        " m.vehicle=e.vehicle AND m.month=e.scope AND m.digest=e.digest)) OR"
        " (kind='page' AND EXISTS (SELECT 1 FROM history_pages p WHERE"
        " p.vehicle=e.vehicle AND p.plan||':'||p.month||':'||p.page=e.scope AND p.digest=e.digest))"
    )
