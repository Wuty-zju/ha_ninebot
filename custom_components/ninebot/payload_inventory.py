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


def advance(db: sqlite3.Connection) -> None:
    value = revision(db)
    if value == MAX_REVISION:
        raise ValueError("Payload inventory revision exhausted")
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, str(value + 1)),
    )
