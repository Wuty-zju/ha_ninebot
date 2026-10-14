"""Private page evidence and full-history roots within the archive SQL actor.

The caller supplies one account-owned transaction and capacity/backup barriers.
No network or independent connection/writer exists in this module.
"""

import sqlite3
from dataclasses import dataclass

from .archive_codec import decoded, encoded, stamp
from .const import MAX_RESPONSE_BYTES
from .detail_codec import packed, unpacked
from .history_plan import MAX_PAGES, HistoryPlan, HistoryState, plan_data, restored
from .raw import Endpoint, RawRecord
from .travel import MAX_RIDES, opaque_id

SCHEMA: tuple[str, ...] = (
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
    "CREATE TABLE history_ids (vehicle TEXT NOT NULL REFERENCES history_roots(vehicle),"
    " plan TEXT NOT NULL, month TEXT NOT NULL, ride TEXT NOT NULL, first_page INTEGER NOT NULL,"
    " PRIMARY KEY(vehicle,plan,month,ride))",
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
    "history_ids": ("vehicle", "plan", "month", "ride", "first_page"),
}
LEGACY_COLUMNS = COLUMNS
PAYLOAD_SCHEMA = ("CREATE TABLE history_payloads (digest TEXT PRIMARY KEY, body BLOB NOT NULL)",)
SCHEMA = (*SCHEMA, *PAYLOAD_SCHEMA)
COLUMNS = {**COLUMNS, "history_payloads": ("digest", "body")}
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


def resolve(db: sqlite3.Connection, vehicle: str, identity: str) -> HistoryPlan | None:
    baseline = root(db, vehicle)
    if baseline is not None and baseline.plan_id == identity:
        return baseline
    from .incremental_runs import get

    return get(db, vehicle, identity)


def put_root(db: sqlite3.Connection, plan: HistoryPlan, expected: HistoryPlan | None) -> None:
    value = encoded(plan_data(plan))
    current = (
        root(db, plan.vehicle) if expected is None else resolve(db, plan.vehicle, plan.plan_id)
    )
    if current != expected or (
        expected is not None
        and (
            plan.plan_id != expected.plan_id
            or plan.revision != expected.revision + 1
            or plan.start_month != expected.start_month
            or plan.end_month != expected.end_month
            or plan.cutoff != expected.cutoff
            or plan.provider != expected.provider
            or stamp(plan.updated_at) < stamp(expected.updated_at)
        )
    ):
        raise HistoryConflict
    count, verified = db.execute(
        "SELECT count(*),coalesce(sum(verified),0) FROM history_months WHERE vehicle=? AND plan=?",
        (plan.vehicle, plan.plan_id),
    ).fetchone()
    if count != plan.scanned_months or verified != plan.verified_months:
        raise ValueError("Checkpoint has no monthly evidence")
    if plan.next_month is not None:
        count, maximum = db.execute(
            "SELECT count(*),coalesce(max(page),0) FROM history_pages"
            " WHERE vehicle=? AND plan=? AND month=?",
            (plan.vehicle, plan.plan_id, plan.next_month),
        ).fetchone()
        if count != plan.next_page - 1 or maximum != count:
            raise ValueError("Checkpoint has no page evidence")
    if plan.state in {HistoryState.COMPLETE, HistoryState.WITH_GAPS}:
        from .detail_health import summary

        health = summary(db, plan.vehicle, plan.plan_id)
        if (
            health.missing
            or health.unmapped
            or health.quality_pending
            or plan.state is HistoryState.COMPLETE
            and health.archive_has_gaps
        ):
            raise ValueError("Baseline has unresolved detail evidence")
    baseline = root(db, plan.vehicle)
    if (
        baseline is None or baseline.plan_id == plan.plan_id
    ) and plan.state is HistoryState.RECONCILED:
        raise ValueError("First baseline cannot be locally skipped")
    if baseline is not None and baseline.plan_id != plan.plan_id:
        from .incremental_runs import put

        put(db, plan)
        return
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
    if resolve(db, expected.vehicle, expected.plan_id) != expected or expected.state not in {
        HistoryState.DISCOVER,
        HistoryState.QUERYING,
    }:
        raise HistoryConflict
    if (
        record.query_month != expected.next_month
        or record.endpoint_version != expected.provider
        or record.backend_version != "0.1.7"
    ):
        raise ValueError("Invalid history page source")
    digest, body, clean = packed(record, source=Endpoint.TRAVEL)
    assert clean.query_month is not None
    previous_body = db.execute(
        "SELECT CASE WHEN length(body)<=? THEN body END FROM history_payloads WHERE digest=?",
        (MAX_RESPONSE_BYTES + 1024, digest),
    ).fetchone()
    if previous_body is None:
        previous_body = db.execute(
            "SELECT CASE WHEN length(body)<=? THEN body END FROM history_pages"
            " WHERE digest=? AND length(body)>0 LIMIT 1",
            (MAX_RESPONSE_BYTES + 1024, digest),
        ).fetchone()
    if previous_body is None:
        db.execute("INSERT INTO history_payloads VALUES (?,?)", (digest, body))
    else:
        # Verify reuse; a damaged legacy blob must not masquerade as a newly saved page.
        unpacked(
            previous_body[0],
            digest,
            clean.received_at,
            clean.query_month,
            clean.backend_version,
            clean.endpoint_version,
            source=Endpoint.TRAVEL,
        )
    payload = clean.payload()
    assert isinstance(payload, dict)
    ids, total, stop = _shape(payload)
    observed = clean.received_at.isoformat()
    if stamp(observed) < stamp(expected.updated_at):
        raise ValueError("Stale history receipt")
    scope = (expected.vehicle, expected.plan_id, expected.next_month)
    count, maximum = db.execute(
        "SELECT count(*),coalesce(max(page),0) FROM history_pages"
        " WHERE vehicle=? AND plan=? AND month=?",
        scope,
    ).fetchone()
    if count != expected.next_page - 1 or maximum != count:
        raise ValueError("Invalid history page checkpoint")
    changed_total = db.execute(
        "SELECT 1 FROM history_pages WHERE vehicle=? AND plan=? AND month=?"
        " AND (total IS NULL OR total != ?) LIMIT 1",
        (*scope, total),
    ).fetchone()
    if changed_total:
        stop = stop or "unstable_total"
    before = db.execute(
        "SELECT count(*) FROM history_ids WHERE vehicle=? AND plan=? AND month=?", scope
    ).fetchone()[0]
    db.executemany(
        "INSERT OR IGNORE INTO history_ids VALUES (?,?,?,?,?)",
        ((*scope, identity, expected.next_page) for identity in ids),
    )
    known = db.execute(
        "SELECT count(*) FROM history_ids WHERE vehicle=? AND plan=? AND month=?", scope
    ).fetchone()[0]
    if ids and known == before:
        stop = stop or "repeated_page"
    if total is not None and known > total:
        stop = stop or "unstable_total"
    verified = stop is None and total == known
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
            b"",
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
                known,
                total,
                int(verified),
                stop,
                observed,
            ),
        )
    put_root(db, following, expected)
    return PageReceipt(following, known, total, finished, verified, stop)


def page(
    db: sqlite3.Connection, vehicle: str, plan: str, month: str, number: int
) -> RawRecord | None:
    cap = MAX_RESPONSE_BYTES + 1024
    row = db.execute(
        "SELECT CASE WHEN length(p.body)>0 THEN CASE WHEN length(p.body)<=? THEN p.body END"
        " ELSE CASE WHEN h.digest IS NOT NULL THEN CASE WHEN length(h.body)<=? THEN h.body END"
        " ELSE (SELECT old.body FROM history_pages old WHERE old.digest=p.digest"
        " AND length(old.body)>0 AND length(old.body)<=? LIMIT 1) END END,"
        " p.digest,p.observed,p.backend,p.endpoint FROM history_pages p"
        " LEFT JOIN history_payloads h ON h.digest=p.digest"
        " WHERE p.vehicle=? AND p.plan=? AND p.month=? AND p.page=?",
        (cap, cap, cap, vehicle, plan, month, number),
    ).fetchone()
    return (
        unpacked(row[0], row[1], stamp(row[2]), month, row[3], row[4], source=Endpoint.TRAVEL)
        if row
        else None
    )


def check(db: sqlite3.Connection, *, allow_runs: bool = True) -> None:
    current: HistoryPlan | None
    roots = list(db.execute("SELECT * FROM history_roots"))
    if allow_runs:
        roots.extend(
            (restored(decoded(value)).vehicle, value)
            for (value,) in db.execute("SELECT value FROM meta WHERE key LIKE 'history_run:%'")
        )
    for vehicle, value in roots:
        current = restored(decoded(value))
        baseline = root(db, vehicle)
        if (
            baseline is not None
            and baseline.plan_id == current.plan_id
            and current.state is HistoryState.RECONCILED
        ):
            raise ValueError("First baseline cannot be locally skipped")
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
        current = resolve(db, vehicle, plan)
        if (
            current is None
            or plan != current.plan_id
            or not current.start_month <= month <= current.end_month
            or current.next_month is not None
            and month <= current.next_month
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
        indexed = db.execute(
            "SELECT count(*) FROM history_ids WHERE vehicle=? AND plan=? AND month=?",
            (vehicle, plan, month),
        ).fetchone()[0]
        if indexed != known:
            raise ValueError("Invalid history identity index")
        stamp(observed)
    if db.execute(
        "SELECT 1 FROM history_ids i LEFT JOIN history_pages p ON i.vehicle=p.vehicle"
        " AND i.plan=p.plan AND i.month=p.month AND i.first_page=p.page"
        " WHERE p.page IS NULL LIMIT 1"
    ).fetchone():
        raise ValueError("History identity has no source page")
    for identity, number in db.execute("SELECT ride,first_page FROM history_ids"):
        if (
            not isinstance(identity, str)
            or opaque_id(identity) != identity
            or type(number) is not int
            or not 1 <= number <= MAX_PAGES
        ):
            raise ValueError("Invalid history identity index metadata")
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
        current = resolve(db, vehicle, plan)
        if (
            current is None
            or plan != current.plan_id
            or not current.start_month <= month <= current.end_month
            or type(number) is not int
            or not 1 <= number <= MAX_PAGES
            or kind != "blob"
            or size < 0
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
    if allow_runs:
        if db.execute(
            "SELECT 1 FROM history_pages p LEFT JOIN history_payloads h ON h.digest=p.digest"
            " WHERE length(p.body)=0 AND h.digest IS NULL AND NOT EXISTS"
            " (SELECT 1 FROM history_pages old WHERE old.digest=p.digest"
            " AND length(old.body)>0) LIMIT 1"
        ).fetchone():
            raise ValueError("Missing shared page payload")
        for digest, kind, size in db.execute(
            "SELECT digest,typeof(body),length(body) FROM history_payloads"
        ):
            if (
                len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or kind != "blob"
                or size < 0
            ):
                raise ValueError("Invalid shared page payload")
