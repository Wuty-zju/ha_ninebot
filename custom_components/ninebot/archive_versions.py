"""Connection-local catalog fences; no persistent schema or extra writer.

TEMP triggers cover managed SQL writers, including membership repairs, in their
own transaction. Per-row revisions are local counters, so MAX(revision) is not
an account or vehicle content version. Connection epochs reject reopen replays.
"""

import secrets
import sqlite3

# This fence describes ride selection/scalars and saved-detail presence. Per-page
# receipt/coverage metadata is refreshed, not an immutable whole-archive snapshot.
WATCHED = {
    "rides": ("id", "data", "started", "ended"),
    "months": ("month", "data", "ids", "backend"),
    "membership": ("month", "ride", "latest"),
    "details": ("ride",),
}


def install(db: sqlite3.Connection) -> None:
    """Install once on a validated actor connection, before accepting operations."""
    db.execute(
        "CREATE TEMP TABLE ninebot_catalog_versions"
        " (vehicle TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
    )
    db.execute("CREATE TEMP TABLE ninebot_catalog_epoch (value TEXT NOT NULL)")
    db.execute("INSERT INTO ninebot_catalog_epoch VALUES (?)", (secrets.token_hex(16),))
    for table, fields in WATCHED.items():
        # Identifiers are private constants above, never client-supplied SQL.
        for operation, references in (
            ("INSERT", ("NEW",)),
            ("DELETE", ("OLD",)),
            ("UPDATE", ("OLD", "NEW")),
        ):
            predicate = " OR ".join(f"OLD.{name} IS NOT NEW.{name}" for name in fields)
            when = (
                f" WHEN OLD.vehicle IS NOT NEW.vehicle OR {predicate}"
                if operation == "UPDATE"
                else ""
            )
            changes = " ".join(
                "INSERT INTO ninebot_catalog_versions VALUES ("
                f"{reference}.vehicle,1) ON CONFLICT(vehicle) DO UPDATE SET revision=revision+1;"
                for reference in references
            )
            db.execute(
                f"CREATE TEMP TRIGGER ninebot_catalog_{table}_{operation.lower()}"
                f" AFTER {operation} ON main.{table}{when} BEGIN {changes} END"
            )
    db.commit()  # TEMP metadata only; no product rows/schema/version are changed.


def catalog_version(db: sqlite3.Connection, vehicle: str) -> str:
    """O(1) per-vehicle fence; raw/geometry/progress writes are separate scopes."""
    epoch = db.execute("SELECT value FROM ninebot_catalog_epoch").fetchone()[0]
    row = db.execute(
        "SELECT revision FROM ninebot_catalog_versions WHERE vehicle=?", (vehicle,)
    ).fetchone()
    return f"{epoch}:{row[0] if row else 0}"
