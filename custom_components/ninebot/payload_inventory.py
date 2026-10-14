"""Persistent fence for managed payload mutations, independent of HA projections.

All archive payload writers advance this inside their existing transaction.
This is not a detector for external file edits or latent disk corruption; those
still require bounded reads/audits. The account-wide fence is conservative.
"""

import sqlite3

KEY = "history_payload_revision"
MAX_REVISION = 2**63 - 1


def validate(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= MAX_REVISION:
        raise ValueError("Invalid payload inventory revision")
    return int(value)


def revision(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
    return validate(row[0]) if row else 0


def vehicle_revision(db: sqlite3.Connection, vehicle: str) -> int:
    if len(vehicle) != 64 or any(c not in "0123456789abcdef" for c in vehicle):
        raise ValueError("Invalid payload vehicle scope")
    row = db.execute("SELECT value FROM meta WHERE key=?", (f"{KEY}:{vehicle}",)).fetchone()
    return validate(row[0]) if row else 0


def advance(db: sqlite3.Connection, vehicle: str) -> None:
    value = revision(db)
    if value == MAX_REVISION:
        raise ValueError("Payload inventory revision exhausted")
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, str(value + 1)),
    )

    scoped = vehicle_revision(db, vehicle)
    if scoped == MAX_REVISION:
        raise ValueError("Payload vehicle revision exhausted")
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"{KEY}:{vehicle}", str(scoped + 1)),
    )
