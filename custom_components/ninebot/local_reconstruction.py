"""Trusted local scalar reconstruction; no network, state attributes or ID guessing.

An independently checked month page binds an ID-less detail to a ride. Only
valid latest-detail facts can amend an index; absent fields retain their prior
values. A successful assessment is not a cloud/reference completeness proof.
"""

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from .archive_codec import RIDE_VALUES, merged_ride, stamp
from .detail_codec import ArchivedDetail, CorruptPayload
from .exceptions import NinebotError
from .history_store import page
from .raw import Endpoint
from .ride_models import Ride
from .travel import parse_ride, parse_rides


@dataclass(frozen=True)
class Reconstruction:
    status: str
    fields: tuple[str, ...] = ()
    repaired: bool = False
    checked_fields: tuple[str, ...] = ()
    source_copy_checks: int = 0
    membership_updates: tuple[tuple[str, int], ...] = ()


def proposal(
    db: sqlite3.Connection,
    vehicle: str,
    current: Ride,
    observed: datetime,
    stored: ArchivedDetail,
) -> tuple[Reconstruction, Ride | None]:
    latest = stored.latest
    if latest is None:
        return Reconstruction("corrupt"), None
    if latest.received_at < observed:
        return Reconstruction("stale"), None
    detail = parse_ride(
        latest.payload(), current.query_month, source=Endpoint.TRIP_DETAIL, max_points=1
    )
    if {"conflicting_ride_ids", "conflicting_detail_ids"} & set(detail.issues):
        return Reconstruction("unverifiable"), None
    if (detail.ride_id is not None and detail.ride_id != current.ride_id) or (
        detail.detail_id is not None and detail.detail_id != current.detail_id
    ):
        return Reconstruction("unverifiable"), None
    identified = detail.ride_id is not None and detail.ride_id == current.ride_id
    if not identified:
        # At most two candidate pages; never scan/decompress the entire month.
        rows = db.execute(
            "SELECT i.plan,i.month,i.first_page FROM history_ids i JOIN history_pages p"
            " ON p.vehicle=i.vehicle AND p.plan=i.plan AND p.month=i.month"
            " AND p.page=i.first_page WHERE i.vehicle=? AND i.ride=? AND i.month=?"
            " AND p.observed<=? ORDER BY p.observed DESC,i.plan LIMIT 2",
            (
                vehicle,
                current.ride_id,
                current.query_month,
                latest.received_at.isoformat(timespec="microseconds"),
            ),
        ).fetchall()
        witnesses: list[Ride] = []
        try:
            for plan, month, number in rows:
                raw = page(db, vehicle, plan, month, number)
                if raw is None:
                    continue
                witnesses.extend(
                    ride
                    for ride in parse_rides(raw.payload(), month)
                    if ride.ride_id == current.ride_id
                )
        except (CorruptPayload, NinebotError, ValueError):
            return Reconstruction("unverifiable"), None
        if not witnesses or detail.started_at is None:
            return Reconstruction("unverifiable"), None
        # Independent list identity plus exact start, never time-based ID discovery.
        for witness in witnesses:
            if witness.detail_id != current.detail_id or witness.started_at != detail.started_at:
                return Reconstruction("unverifiable"), None
            if (
                witness.ended_at is not None
                and detail.ended_at is not None
                and witness.ended_at != detail.ended_at
            ):
                growing = (
                    detail.ended_at > witness.ended_at
                    and detail.duration_s is not None
                    and abs(
                        (detail.ended_at - detail.started_at).total_seconds() - detail.duration_s
                    )
                    <= 1
                )
                if not growing:
                    return Reconstruction("unverifiable"), None
    # Bind reviewed facts to existing identity; do not reconstruct missing
    # detail properties from a stale list or change meaningful entity identity.
    incoming = replace(detail, ride_id=current.ride_id, detail_id=current.detail_id)
    checked = tuple(field for field in RIDE_VALUES if getattr(detail, field) is not None)
    if not checked:
        return Reconstruction("unverifiable"), None
    candidate, fields, metadata_checked = scalar_candidate(current, incoming, checked)
    index = db.execute(
        "SELECT started,ended FROM rides WHERE vehicle=? AND id=?",
        (vehicle, current.ride_id),
    ).fetchone()
    if index is None:
        return Reconstruction("unverifiable"), None
    expected = (
        candidate.started_at.isoformat() if candidate.started_at else "",
        candidate.ended_at.isoformat() if candidate.ended_at else None,
    )
    # Compare timestamp semantics rather than equivalent ISO spelling.
    actual = (stamp(index[0]) if index[0] else None, stamp(index[1]) if index[1] else None)
    normalized = (
        stamp(expected[0]) if expected[0] else None,
        stamp(expected[1]) if expected[1] else None,
    )
    if actual != normalized:
        fields += ("time_index",)
    return Reconstruction(
        "repairable" if fields else "healthy", fields, checked_fields=checked + metadata_checked
    ), candidate


def scalar_candidate(
    current: Ride, incoming: Ride, checked: tuple[str, ...]
) -> tuple[Ride, tuple[str, ...], tuple[str, ...]]:
    """Patch proven scalar facts only; do not rewrite unrelated observation state.

    A later list can retain an older detail value while reporting it missing.
    Its aggregate presence state is legitimate and cannot be repaired from that
    detail. Precision proves a floor, not that earlier extra digits were false.
    """
    merged = merged_ride(current, incoming)
    fields = tuple(field for field in checked if getattr(current, field) != getattr(merged, field))
    metadata_checked: list[str] = []
    metadata = {
        name: dict(getattr(current, name))
        for name in ("field_provenance", "field_sources", "field_states", "detail_field_states")
    }
    incoming_states = dict(incoming.field_states)
    for field in checked:
        owned_by_detail = (
            current.source is Endpoint.TRIP_DETAIL
            or metadata["field_sources"].get(field) == Endpoint.TRIP_DETAIL.value
            or field in fields
        )
        for name in metadata:
            if name in {"field_provenance", "field_sources"}:
                if not owned_by_detail:
                    continue  # A valid list source is not an erroneous detail source.
                expected = dict(getattr(merged, name))
            else:
                if name == "field_states" and current.source is not Endpoint.TRIP_DETAIL:
                    continue  # Keep the last aggregate/list observation's presence.
                expected = incoming_states
            label = f"{name}.{field}"
            metadata_checked.append(label)
            before, after = metadata[name].get(field), expected.get(field)
            if before != after:
                fields += (label,)
            if field in expected:
                metadata[name][field] = expected[field]
            else:
                metadata[name].pop(field, None)
    precision = dict(current.precision)
    available_precision = dict(incoming.precision)
    merged_precision = dict(merged.precision)
    for field, raw_key in (
        ("distance_m", "mileages"),
        ("duration_s", "duration"),
        ("energy_raw", "ec"),
        ("speed_raw", "speed"),
    ):
        if field not in checked or raw_key not in available_precision:
            continue
        label = f"precision_floor.{raw_key}"
        metadata_checked.append(label)
        if precision.get(raw_key) != merged_precision.get(raw_key):
            fields += (label,)
            precision[raw_key] = merged_precision[raw_key]
    # Preserve global parser/identity/source and unrelated issues. The existing
    # archive merge still recomputes the duration/time consistency invariant.
    candidate = replace(
        current,
        **{field: getattr(merged, field) for field in checked},
        field_provenance=tuple(sorted(metadata["field_provenance"].items())),
        field_sources=tuple(sorted(metadata["field_sources"].items())),
        field_states=tuple(sorted(metadata["field_states"].items())),
        detail_field_states=tuple(sorted(metadata["detail_field_states"].items())),
        precision=tuple(sorted(precision.items())),
    )
    return candidate, fields, tuple(metadata_checked)


def quality_patch(db: sqlite3.Connection, vehicle: str, stored: ArchivedDetail) -> str | None:
    from .detail_health import data, key, validate

    if (
        stored.ride.ride_id is None
        or stored.latest_quality is None
        or stored.selected_quality is None
    ):
        return None
    value = data(
        stored.latest_hash, stored.geometry_hash, stored.latest_quality, stored.selected_quality
    )
    row = db.execute(
        "SELECT value FROM meta WHERE key=?", (key(vehicle, stored.ride.ride_id),)
    ).fetchone()
    return value if row is None or validate(row[0]) != validate(value) else None
