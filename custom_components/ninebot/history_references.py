"""Metadata-only consistency for enumerated IDs, not raw/cloud certification.

One actor SELECT returns a scalar count of affected rides. It never opens raw
BLOBs, returns identity data, changes selections or reconstructs missing parents.
"""

import sqlite3

from .gap_recovery import TARGETS

QUERY = (
    f"WITH targets AS ({TARGETS}),"
    " items AS (SELECT mo.month,j.value,j.type,"
    " CASE WHEN json_valid(mo.ids) THEN json_type(mo.ids,'$.ids') IS NOT 'array'"
    " ELSE 1 END AS bad_shape FROM months mo"
    " JOIN (SELECT DISTINCT month FROM targets) t ON t.month=mo.month"
    " LEFT JOIN json_each(CASE WHEN json_valid(mo.ids) THEN mo.ids ELSE '{}' END,'$.ids') j"
    " ON 1 WHERE mo.vehicle=?),"
    " parents AS (SELECT month,MAX(bad_shape) OR count(type)!="
    " count(DISTINCT CASE WHEN type='text' THEN value END) AS invalid"
    " FROM items GROUP BY month),"
    " selected AS (SELECT month,value AS ride FROM items WHERE type='text' GROUP BY month,value),"
    " flags AS (SELECT i.ride,MAX(r.id IS NULL OR p.month IS NULL OR p.invalid OR m.ride IS NULL"
    " OR (m.ride IS NOT NULL AND m.latest!=(s.ride IS NOT NULL))) AS bad"
    " FROM targets i LEFT JOIN rides r ON r.vehicle=? AND r.id=i.ride"
    " LEFT JOIN parents p ON p.month=i.month"
    " LEFT JOIN membership m ON m.vehicle=? AND m.month=i.month AND m.ride=i.ride"
    " LEFT JOIN selected s ON s.month=i.month AND s.ride=i.ride GROUP BY i.ride)"
    " SELECT count(*) FROM flags WHERE bad"
)


def issues(db: sqlite3.Connection, vehicle: str, plan: str) -> int:
    """All referenced months are checked; count each affected ride only once."""
    return int(db.execute(QUERY, (vehicle, plan, plan, vehicle, vehicle, vehicle)).fetchone()[0])
