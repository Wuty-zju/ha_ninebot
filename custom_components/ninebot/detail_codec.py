"""Private, bounded business detail encoding; never HA state or diagnostics."""

import hashlib
import json
import zlib
from dataclasses import dataclass
from datetime import datetime

from .const import MAX_RESPONSE_BYTES
from .raw import Endpoint, RawLimitError, RawRecord, build_record
from .ride_models import Ride, TrackQuality


class CorruptPayload(ValueError):
    """An individual stored business object failed bounded integrity validation."""


@dataclass(frozen=True)
class ArchivedDetail:
    ride: Ride
    latest: RawRecord | None
    geometry: RawRecord | None
    revision: int
    selection: str
    latest_hash: str
    geometry_hash: str
    scalar_revision: int
    scalar_observed_at: datetime
    faults: tuple[str, ...] = ()
    geometry_source: str = "selected_geometry"
    latest_quality: TrackQuality | None = None
    selected_quality: TrackQuality | None = None


def packed(
    record: RawRecord, *, source: Endpoint = Endpoint.TRIP_DETAIL
) -> tuple[str, bytes, RawRecord]:
    """Reapply redaction even to caller-created records; preserve unknown data."""
    if (
        source not in {Endpoint.TRIP_DETAIL, Endpoint.TRAVEL}
        or record.endpoint is not source
        or record.query_month is None
    ):
        raise ValueError("Invalid private detail scope")
    clean = build_record(
        record.endpoint,
        record.payload(),
        record.received_at,
        record.query_month,
        backend_version=record.backend_version,
        endpoint_version=record.endpoint_version,
    )
    if not isinstance(clean.payload(), dict):
        raise ValueError("Invalid private detail shape")
    # Canonical JSON enables content dedup independent of key ordering.
    body = json.dumps(
        clean.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Private detail byte budget")
    return hashlib.sha256(body).hexdigest(), zlib.compress(body), clean


def unpacked(
    body: bytes,
    digest: str,
    received: datetime,
    month: str,
    backend: str | None,
    endpoint: str | None,
    *,
    source: Endpoint = Endpoint.TRIP_DETAIL,
) -> RawRecord:
    """Bound decompression before JSON parsing, and verify payload integrity."""
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES + 1024:
        raise CorruptPayload("Private detail compressed budget")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(body, MAX_RESPONSE_BYTES + 1)
    except zlib.error:
        raise CorruptPayload("Invalid private detail compression") from None
    if (
        len(raw) > MAX_RESPONSE_BYTES
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != digest
    ):
        raise CorruptPayload("Invalid private detail integrity")
    if source not in {Endpoint.TRIP_DETAIL, Endpoint.TRAVEL}:
        raise ValueError("Invalid private source")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CorruptPayload("Invalid private detail JSON") from None
    try:
        record = build_record(
            source,
            payload,
            received,
            month,
            backend_version=backend,
            endpoint_version=endpoint,
        )
    except RawLimitError:
        raise CorruptPayload("Invalid private detail structure") from None
    if not isinstance(record.payload(), dict):
        raise CorruptPayload("Invalid private detail shape")
    return record


def scoped_ride(
    record: RawRecord, current: Ride, *, max_points: int = 1, point_offset: int = 0
) -> Ride:
    """Reject explicit identity contradictions; retain ID-less query provenance."""
    from .travel import parse_ride

    if record.query_month != current.query_month:
        raise CorruptPayload("Private detail month contradiction")
    parsed = parse_ride(
        record.payload(),
        current.query_month,
        source=Endpoint.TRIP_DETAIL,
        max_points=max_points,
        point_offset=point_offset,
    )
    if (
        {"conflicting_ride_ids", "conflicting_detail_ids"} & set(parsed.issues)
        or (parsed.ride_id is not None and parsed.ride_id != current.ride_id)
        or (parsed.detail_id is not None and parsed.detail_id != current.detail_id)
    ):
        raise CorruptPayload("Private detail identity contradiction")
    return parsed
