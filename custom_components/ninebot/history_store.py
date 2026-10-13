"""Private page evidence and full-history roots within the archive SQL actor.

The caller supplies one account-owned transaction and capacity/backup barriers.
No network or independent connection/writer exists in this module.
"""

import sqlite3
from dataclasses import dataclass

from .archive_codec import decoded, encoded, stamp
from .detail_codec import packed, unpacked
from .history_plan import MAX_PAGES, HistoryPlan, HistoryState, plan_data, restored
from .raw import Endpoint, RawRecord
from .travel import MAX_RIDES, opaque_id

SCHEMA = (
    "CREATE TABLE history_roots (vehicle TEXT PRIMARY KEY REFERENCES vehicles(vehicle),"
    " data TEXT NOT NULL)",
    "CREATE TABLE history_pages (vehicle TEXT NOT NULL REFERENCES history_roots(vehicle),"
    " plan TEXT NOT NULL, month TEXT NOT NULL, page INTEGER NOT NULL, digest TEXT NOT NULL,"
    " body BLOB NOT NULL, observed TEXT NOT NULL,"
    " backend TEXT, endpoint TEXT NOT NULL, ids TEXT NOT NULL, total INTEGER, stop TEXT,"
    " PRIMARY KEY(vehicle,plan,month,page))",
    "CREATE TABLE history_months (vehicle TEXT NOT NULL REFERENCES history_roots(vehicle),"
    " plan TEXT NOT NULL, month TEXT NOT NULL, pages INTEGER NOT NULL, known INTEGER NOT NULL,"
    " total INTEGER, verified INTEGER NOT NULL CHECK(verified IN (0,1)), stop TEXT,"
    " observed TEXT NOT NULL, PRIMARY KEY(vehicle,plan,month))",
)
COLUMNS = {
    "history_roots": ("vehicle", "data"),
    "history_pages": (
        "vehicle",
        "plan",
        "month",
        "page",
        "digest",
        "body",
        "observed",
        "backend",
        "endpoint",
        "ids",
        "total",
        "stop",
    ),
    "history_months": (
        "vehicle",
        "plan",
        "month",
        "pages",
        "known",
        "total",
        "verified",
        "stop",
        "observed",
    ),
}
STOP_REASONS = {
    None,
    "schema",
    "missing_total",
    "unstable_total",
    "empty_page",
    "repeated_page",
    "page_limit",
}


class HistoryConflict(Exception):
    """Stale root ownership/revision; no evidence or checkpoint was changed."""


@dataclass(frozen=True)
class PageReceipt:
    root: HistoryPlan
    unique_count: int
    total: int | None
    finished: bool
    verified: bool
    stop: str | None


def root(db: sqlite3.Connection, vehicle: str) -> HistoryPlan | None:
    row = db.execute("SELECT data FROM history_roots WHERE vehicle=?", (vehicle,)).fetchone()
    return restored(decoded(row[0])) if row else None


def put_root(db: sqlite3.Connection, plan: HistoryPlan, expected: HistoryPlan | None) -> None:
    value = encoded(plan_data(plan))
    current = root(db, plan.vehicle)
    if current != expected or (
        expected is not None
        and (
            plan.plan_id != expected.plan_id
            or plan.revision != expected.revision + 1
            or plan.start_month != expected.start_month
            or plan.end_month != expected.end_month
            or plan.cutoff != expected.cutoff
            or plan.provider != expected.provider
        )
    ):
        raise HistoryConflict
    db.execute(
        "INSERT INTO history_roots VALUES (?,?) ON CONFLICT(vehicle)"
        " DO UPDATE SET data=excluded.data",
        (plan.vehicle, value),
    )


def _shape(data: dict) -> tuple[tuple[str, ...], int | None, str | None]:
    total = data.get("times")
    if isinstance(total, str) and total.isascii() and total.isdecimal() and len(total) <= 7:
        total = int(total)
    if type(total) is not int or not 0 <= total <= 1_000_000:
        total = None
    if "list" not in data:
        return (), total, "schema"
    rows = data.get("list")
    # null is an observed empty-list representation, not proof of zero rides.
    if rows is None:
        rows = []
    if not isinstance(rows, list) or len(rows) > MAX_RIDES:
        return (), total, "schema"
    ids = []
    for row in rows:
        identity = opaque_id(row.get("travel_id")) if isinstance(row, dict) else None
        if identity is None or identity in ids:
            return (), total, "schema"
        ids.append(identity)
    return tuple(ids), total, None if total is not None else "missing_total"


def accept_page(db: sqlite3.Connection, expected: HistoryPlan, record: RawRecord) -> PageReceipt:
    if root(db, expected.vehicle) != expected or expected.state is not HistoryState.DISCOVER:
        raise HistoryConflict
    if (
        record.query_month != expected.next_month
        or record.endpoint_version != expected.provider
        or record.backend_version != "0.1.7"
    ):
        raise ValueError("Invalid history page source")
    digest, body, clean = packed(record, source=Endpoint.TRAVEL)
    payload = clean.payload()
    assert isinstance(payload, dict)
    ids, total, stop = _shape(payload)
    observed = clean.received_at.isoformat()
    if stamp(observed) < stamp(expected.updated_at):
        raise ValueError("Stale history receipt")
    previous = db.execute(
        "SELECT page,ids,total FROM history_pages WHERE vehicle=? AND plan=? AND month=?"
        " ORDER BY page",
        (expected.vehicle, expected.plan_id, expected.next_month),
    ).fetchall()
    if [row[0] for row in previous] != list(range(1, expected.next_page)):
        raise ValueError("Invalid history page checkpoint")
    seen: set[str] = set()
    for _, values, previous_total in previous:
        seen.update(decoded(values)["ids"])
        if previous_total != total:
            stop = stop or "unstable_total"
    if ids and not set(ids) - seen:
        stop = stop or "repeated_page"
    seen.update(ids)
    if total is not None and len(seen) > total:
        stop = stop or "unstable_total"
    verified = stop is None and total == len(seen)
    if not verified and not ids:
        stop = stop or "empty_page"
    if not verified and expected.next_page == MAX_PAGES:
        stop = stop or "page_limit"
    finished = verified or stop is not None
    db.execute(
        "INSERT INTO history_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            expected.vehicle,
            expected.plan_id,
            expected.next_month,
            expected.next_page,
            digest,
            body,
            observed,
            clean.backend_version,
            clean.endpoint_version,
            encoded({"ids": ids}),
            total,
            stop,
        ),
    )
    following = expected.page_committed(observed, finished=finished, verified=verified)
    if finished:
        db.execute(
            "INSERT INTO history_months VALUES (?,?,?,?,?,?,?,?,?)",
            (
                expected.vehicle,
                expected.plan_id,
                expected.next_month,
                expected.next_page,
                len(seen),
                total,
                int(verified),
                stop,
                observed,
            ),
        )
    put_root(db, following, expected)
    return PageReceipt(following, len(seen), total, finished, verified, stop)


def page(
    db: sqlite3.Connection, vehicle: str, plan: str, month: str, number: int
) -> RawRecord | None:
    row = db.execute(
        "SELECT body,digest,observed,backend,endpoint FROM history_pages"
        " WHERE vehicle=? AND plan=? AND month=? AND page=?",
        (vehicle, plan, month, number),
    ).fetchone()
    return (
        unpacked(row[0], row[1], stamp(row[2]), month, row[3], row[4], source=Endpoint.TRAVEL)
        if row
        else None
    )


def check(db: sqlite3.Connection) -> None:
    for vehicle, value in db.execute("SELECT * FROM history_roots"):
        current = restored(decoded(value))
        if current.vehicle != vehicle:
            raise ValueError("Invalid history root identity")
        count, verified = db.execute(
            "SELECT count(*),coalesce(sum(verified),0) FROM history_months"
            " WHERE vehicle=? AND plan=?",
            (vehicle, current.plan_id),
        ).fetchone()
        if count != current.scanned_months or verified != current.verified_months:
            raise ValueError("Invalid history manifest checkpoint")
        if current.next_month is not None:
            count, maximum = db.execute(
                "SELECT count(*),coalesce(max(page),0) FROM history_pages"
                " WHERE vehicle=? AND plan=? AND month=?",
                (vehicle, current.plan_id, current.next_month),
            ).fetchone()
            if count != current.next_page - 1 or maximum != count:
                raise ValueError("Invalid history page cursor")
    for vehicle, plan, month, pages, known, total, verified, stop, observed in db.execute(
        "SELECT * FROM history_months"
    ):
        current = root(db, vehicle)
        if (
            current is None
            or plan != current.plan_id
            or not current.start_month <= month <= current.end_month
            or type(pages) is not int
            or not 1 <= pages <= MAX_PAGES
            or type(known) is not int
            or not 0 <= known <= 1_000_000
            or verified not in (0, 1)
            or stop not in STOP_REASONS
            or bool(verified) != (stop is None)
            or verified
            and total != known
        ):
            raise ValueError("Invalid history monthly manifest")
        count, maximum = db.execute(
            "SELECT count(*),max(page) FROM history_pages WHERE vehicle=? AND plan=? AND month=?",
            (vehicle, plan, month),
        ).fetchone()
        if count != pages or maximum != pages:
            raise ValueError("Missing history manifest evidence")
        stamp(observed)
    for (
        vehicle,
        plan,
        month,
        number,
        digest,
        kind,
        size,
        observed,
        backend,
        endpoint,
        ids,
        total,
        stop,
    ) in db.execute(
        "SELECT vehicle,plan,month,page,digest,typeof(body),length(body),observed,backend,"
        "endpoint,ids,total,stop FROM history_pages"
    ):
        current = root(db, vehicle)
        if (
            current is None
            or plan != current.plan_id
            or not current.start_month <= month <= current.end_month
            or type(number) is not int
            or not 1 <= number <= MAX_PAGES
            or kind != "blob"
            or not 0 < size <= 1024 * 1024 + 1024
            or endpoint != current.provider
            or backend != "0.1.7"
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or stop not in STOP_REASONS
        ):
            raise ValueError("Invalid history page metadata")
        stamp(observed)
        values = decoded(ids)
        if (
            set(values) != {"ids"}
            or not isinstance(values["ids"], list)
            or len(values["ids"]) > MAX_RIDES
            or any(opaque_id(i) != i for i in values["ids"])
            or len(set(values["ids"])) != len(values["ids"])
            or total is not None
            and (type(total) is not int or not 0 <= total <= 1_000_000)
        ):
            raise ValueError("Invalid history page identities")
