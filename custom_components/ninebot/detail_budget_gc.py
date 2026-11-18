"""Bounded expiry of task counters; no permission, request or history deletion."""

import json
import math
import re
import sqlite3
from datetime import datetime

PREFIX = "assistant_attempts:"
SINCE = "assistant_budget_gc_since"
CURSOR = "assistant_budget_gc_cursor"
PAGE_SIZE = 64
LEGACY_GRACE = 24 * 3600


def prune(db: sqlite3.Connection, now: datetime) -> int:
    from .preservation import DetailBudget, DetailBudgetUnavailable

    epoch = now.timestamp()
    with db:
        row = db.execute("SELECT value FROM meta WHERE key=?", (SINCE,)).fetchone()
        if row is None:
            since = epoch
            db.execute("INSERT INTO meta VALUES (?,?)", (SINCE, str(since)))
        else:
            try:
                since = float(row[0])
            except (ValueError, TypeError):
                return 0
            if not math.isfinite(since) or since < 0 or since > epoch:
                return 0  # Unknown marker/clock rollback cannot justify deletion.
        row = db.execute("SELECT value FROM meta WHERE key=?", (CURSOR,)).fetchone()
        cursor = row[0] if row and isinstance(row[0], str) else ""
        if cursor and (not cursor.startswith(PREFIX) or len(cursor) != len(PREFIX) + 32):
            return 0
        rows = db.execute(
            "SELECT key,CASE WHEN length(value)<=512 THEN value END FROM meta "
            "WHERE key GLOB 'assistant_attempts:*' AND length(key)=? AND key>? "
            "ORDER BY key LIMIT ?",
            (len(PREFIX) + 32, cursor, PAGE_SIZE),
        ).fetchall()
        removed = 0
        for key, value in rows:
            if re.fullmatch(r"[a-f0-9]{32}", key[len(PREFIX) :]) is None:
                continue
            try:
                raw = json.loads(value)
                signature = raw["scope_hash"]
                if (
                    not isinstance(signature, str)
                    or re.fullmatch(r"[a-f0-9]{64}", signature) is None
                ):
                    continue
                verified = DetailBudget(key[len(PREFIX) :], raw["limit"]).read(value, signature)
            except (DetailBudgetUnavailable, ValueError, TypeError, KeyError, RecursionError):
                continue  # Preserve unknown/damaged metadata for inspection.
            expires = verified.get("expires_at")
            if (expires is not None and expires <= epoch) or (
                expires is None and epoch - since >= LEGACY_GRACE
            ):
                removed += db.execute(
                    "DELETE FROM meta WHERE key=? AND value=?", (key, value)
                ).rowcount
        # Forward scan prevents live counters at the front from starving expired ones.
        next_cursor = rows[-1][0] if rows else ""
        db.execute(
            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CURSOR, next_cursor),
        )
        return removed
