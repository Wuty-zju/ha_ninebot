"""Persistent bounded keyset search of managed local recovery-copy names.

A captured name interval is not an immutable filesystem snapshot. Every selected
payload still needs owner, metadata and content validation. No path is accepted
from an Action, entity or archive metadata.
"""

import hashlib
import heapq
import os
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .archive_codec import decoded, encoded
from .object_checks import digest

PREFIX = "payload_search:"
BATCH = 8


@dataclass(frozen=True)
class Search:
    job_id: str
    upper: str | None
    after: str | None = None
    checked: int = 0
    finished: bool = False


def token(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{32}", value):
        raise ValueError("Invalid managed recovery identity")


def key(vehicle: str, ride: str, expected: str) -> str:
    digest(vehicle)
    digest(expected)
    identity = hashlib.sha256((ride + "\0" + expected).encode()).hexdigest()
    return PREFIX + vehicle + ":" + identity


def restored(value: str) -> Search:
    data = decoded(value)
    if set(data) != set(Search.__dataclass_fields__):
        raise ValueError("Invalid recovery search fields")
    token(data["job_id"])
    for field in ("upper", "after"):
        if data[field] is not None:
            token(data[field])
    if (
        type(data["checked"]) is not int
        or not 0 <= data["checked"] <= 2**63 - 1
        or type(data["finished"]) is not bool
        or (data["after"] is not None and (data["upper"] is None or data["after"] > data["upper"]))
    ):
        raise ValueError("Invalid recovery search cursor")
    return Search(**data)


def check(key_value: str, value: str) -> None:
    parts = key_value.removeprefix(PREFIX).split(":")
    if len(parts) != 2:
        raise ValueError("Invalid recovery search scope")
    for part in parts:
        digest(part)
    restored(value)


def names(path: Path) -> Iterator[str]:
    pattern = re.compile(re.escape(path.stem) + r"\.recovery-([0-9a-f]{32})\.sqlite3")
    with os.scandir(path.parent) as entries:
        for entry in entries:
            match = pattern.fullmatch(entry.name)
            if match:
                try:
                    if entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                        yield match[1]
                except OSError:
                    continue


def get(db: sqlite3.Connection, scope: str, path: Path, job_id: str) -> Search:
    token(job_id)
    row = db.execute("SELECT value FROM meta WHERE key=?", (scope,)).fetchone()
    previous = restored(row[0]) if row else None
    if previous and previous.job_id == job_id:
        return previous
    upper = max(names(path), default=None)
    return Search(job_id, upper, finished=upper is None)


def page(path: Path, search: Search, limit: int) -> tuple[tuple[str, ...], bool]:
    if search.finished:
        return (), False
    values = heapq.nsmallest(
        limit + 1,
        (
            name
            for name in names(path)
            if search.upper is not None
            and name <= search.upper
            and (search.after is None or name > search.after)
        ),
    )
    return tuple(values[:limit]), len(values) > limit


def put(db: sqlite3.Connection, scope: str, search: Search) -> None:
    value = encoded(asdict(search))
    check(scope, value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (scope, value),
    )
