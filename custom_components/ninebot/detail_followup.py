"""Private content comparison markers; existing task checkpoint shape stays intact."""

import sqlite3

from .object_checks import digest
from .travel import opaque_id

PREFIX = "preservation_detail:"


def key(vehicle: str, ride: str) -> str:
    digest(vehicle)
    if opaque_id(ride) != ride:
        raise ValueError("Invalid follow-up ride identity")
    return f"{PREFIX}{vehicle}:{ride}"


def check(scope: str, value: str) -> None:
    vehicle, ride = scope.removeprefix(PREFIX).split(":", 1)
    if key(vehicle, ride) != scope:
        raise ValueError("Invalid follow-up scope")
    if value != "none":
        digest(value)


def put(db: sqlite3.Connection, vehicle: str, ride: str, content_hash: str | None) -> None:
    scope, value = key(vehicle, ride), content_hash or "none"
    check(scope, value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (scope, value),
    )


def get(db: sqlite3.Connection, vehicle: str, ride: str) -> str | None:
    found = db.execute("SELECT value FROM meta WHERE key=?", (key(vehicle, ride),)).fetchone()
    if found is None or found[0] == "none":
        return None
    check(key(vehicle, ride), found[0])
    return found[0]
