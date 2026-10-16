"""Reviewed private quality index; no coordinates, payloads or guessed units."""

import sqlite3
from dataclasses import dataclass

from .archive_codec import decoded, encoded
from .const import MAX_RESPONSE_BYTES
from .ride_models import GeometryKind, TrackQuality
from .travel import opaque_id

PREFIX = "detail_health:"
FIELDS = {
    "latest_hash",
    "geometry_hash",
    "latest_kind",
    "geometry_kind",
    "raw_points",
    "valid_points",
    "invalid_points",
    "scan_complete",
    "latest_simplified",
}


def key(vehicle: str, ride: str) -> str:
    if (
        len(vehicle) != 64
        or any(c not in "0123456789abcdef" for c in vehicle)
        or opaque_id(ride) != ride
    ):
        raise ValueError("Invalid detail health scope")
    return f"{PREFIX}{vehicle}:{ride}"


def validate(value: str) -> dict:
    data = decoded(value)
    if set(data) != FIELDS:
        raise ValueError("Invalid detail health fields")
    for name in ("latest_hash", "geometry_hash"):
        digest = data[name]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Invalid detail health digest")
    GeometryKind(data["latest_kind"])
    GeometryKind(data["geometry_kind"])
    for name in ("raw_points", "valid_points", "invalid_points"):
        if data[name] is not None and (
            type(data[name]) is not int or not 0 <= data[name] <= MAX_RESPONSE_BYTES
        ):
            raise ValueError("Invalid detail health count")
    if (
        type(data["scan_complete"]) is not bool
        or data["latest_simplified"] is not None
        and type(data["latest_simplified"]) is not bool
    ):
        raise ValueError("Invalid detail health flags")
    return data


def data(latest_hash: str, geometry_hash: str, latest: TrackQuality, geometry: TrackQuality) -> str:
    """Canonical derived quality facts, recomputable from retained originals."""
    return encoded(
        {
            "latest_hash": latest_hash,
            "geometry_hash": geometry_hash,
            "latest_kind": latest.kind.value,
            "geometry_kind": geometry.kind.value,
            "raw_points": geometry.raw_point_count,
            "valid_points": geometry.valid_point_count,
            "invalid_points": geometry.invalid_point_count,
            "scan_complete": geometry.scan_complete,
            "latest_simplified": latest.server_simplified,
        }
    )


def put(
    db: sqlite3.Connection,
    vehicle: str,
    ride: str,
    latest_hash: str,
    geometry_hash: str,
    latest: TrackQuality,
    geometry: TrackQuality,
) -> None:
    value = data(latest_hash, geometry_hash, latest, geometry)
    validate(value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key(vehicle, ride), value),
    )


@dataclass(frozen=True)
class HistoryHealth:
    discovered: int
    saved: int
    missing: int
    unmapped: int
    quality_pending: int
    simplified: int
    single_point: int
    unavailable_track: int
    latest_degraded: int
    reference_issues: int = 0

    @property
    def archive_has_gaps(self) -> bool:
        return bool(self.simplified or self.single_point or self.unavailable_track)


def summary(db: sqlite3.Connection, vehicle: str, plan: str) -> HistoryHealth:
    from .gap_recovery import TARGETS

    row = db.execute(
        f"WITH targets AS (SELECT DISTINCT ride FROM ({TARGETS})),"
        " facts AS (SELECT d.ride IS NOT NULL AS saved,"
        " r.id IS NULL OR json_extract(r.data,'$.detail_id') IS NULL AS unmapped,"
        " CASE WHEN json_extract(m.value,'$.latest_hash')=d.latest_hash"
        " AND json_extract(m.value,'$.geometry_hash')=d.geometry_hash THEN m.value END AS health"
        " FROM targets t LEFT JOIN rides r ON r.vehicle=? AND r.id=t.ride"
        " LEFT JOIN details d ON d.vehicle=? AND d.ride=t.ride"
        " LEFT JOIN meta m ON m.key='detail_health:'||?||':'||t.ride)"
        " SELECT count(*),coalesce(sum(saved),0),coalesce(sum(NOT saved),0),"
        " coalesce(sum(unmapped),0),coalesce(sum(saved AND health IS NULL),0),"
        " coalesce(sum(json_extract(health,'$.geometry_kind')='server_simplified'),0),"
        " coalesce(sum(json_extract(health,'$.geometry_kind')='single_point'),0),"
        " coalesce(sum(json_extract(health,'$.geometry_kind') IN ('unknown','empty','invalid')"
        " OR json_extract(health,'$.invalid_points')>0"
        " OR json_extract(health,'$.scan_complete')!=1),0),"
        " coalesce(sum(json_extract(health,'$.latest_simplified')=1),0) FROM facts",
        (vehicle, plan, plan, vehicle, vehicle, vehicle),
    ).fetchone()
    from .history_references import issues

    return HistoryHealth(
        discovered=row[0],
        saved=row[1],
        missing=row[2],
        unmapped=row[3],
        quality_pending=row[4],
        simplified=row[5],
        single_point=row[6],
        unavailable_track=row[7],
        latest_degraded=row[8],
        reference_issues=issues(db, vehicle, plan),
    )
