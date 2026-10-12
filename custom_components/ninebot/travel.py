"""Explicit ninecli 0.1.7 travel contracts, not speculative alias guessing.

List/detail distance km and server scalar maximum speed km/h are ninecli's
display contract. Unix-second times and China time strings were cross-checked
against 20 recorded rows. Trail syntax is lon,lat,speed,distFromPrev separated
by semicolons; point speed/delta units and coordinate reference remain unknown.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .const import BUSINESS_TIMEZONE, MAX_RESPONSE_BYTES
from .exceptions import ErrorKind, NinebotError
from .parsing import number, numeric_precision, payload, previous_month, text
from .raw import Endpoint
from .ride_models import FieldState, GeometryKind, Ride, RideTrackPoint, SpeedSample, TrackQuality

MAX_RIDES = 1000
MAX_TRACK_POINTS = 2000


def opaque_id(value: object) -> str | None:
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and any(ord(char) < 32 for char in value):
        return None
    value = text(value)
    if value is not None and len(value) <= 256 and not any(ord(char) < 32 for char in value):
        return value
    return None


def timestamp(value: object, *, formatted: bool = False) -> datetime | None:
    if formatted:
        if not isinstance(value, str) or len(value) != 19:
            return None
        try:
            result = (
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=ZoneInfo(BUSINESS_TIMEZONE))
                .astimezone(UTC)
            )
        except ValueError:
            return None
    else:
        # Observed integer Unix seconds. Do not guess milliseconds/float epochs.
        if type(value) is not int or not 946684800 <= value <= 4102444799:
            return None
        result = datetime.fromtimestamp(value, UTC)
    return result if 2000 <= result.year <= 2099 else None


def scan_track(
    value: object, max_points: int, point_offset: int = 0
) -> tuple[tuple[RideTrackPoint, ...], TrackQuality, tuple[str, ...]]:
    """Validate every row within the response byte budget; only retain the view."""
    if type(point_offset) is not int or not 0 <= point_offset <= MAX_RESPONSE_BYTES:
        raise ValueError("Invalid point offset")
    if not 1 <= max_points <= MAX_TRACK_POINTS:
        raise ValueError("Invalid point limit")
    if value is None:
        return (), TrackQuality(field_state=FieldState.NULL), ()
    if not isinstance(value, str):
        return (
            (),
            TrackQuality(kind=GeometryKind.INVALID, field_state=FieldState.INVALID),
            ("unverified_track_format",),
        )
    if len(value) > MAX_RESPONSE_BYTES:
        raise NinebotError(ErrorKind.PROTOCOL)
    if value == "":
        return (
            (),
            TrackQuality(
                kind=GeometryKind.EMPTY,
                field_state=FieldState.EMPTY,
                raw_point_count=0,
                valid_point_count=0,
                invalid_point_count=0,
                consecutive_duplicate_count=0,
                scan_complete=True,
            ),
            (),
        )
    # Scan offsets rather than materializing an unbounded split list of rows.
    result = []
    valid = invalid = duplicates = sequence = 0
    previous = None
    offset = 0
    while offset < len(value):
        end = value.find(";", offset)
        if end < 0:
            end = len(value)
        fields = value[offset:end].split(",", 4)
        point = None
        if len(fields) == 4:
            lon, lat = number(fields[0], -180, 180), number(fields[1], -90, 90)
            speed, delta = number(fields[2], 0), number(fields[3])
            if lon is not None and lat is not None and speed is not None and delta is not None:
                point = RideTrackPoint(lat, lon, sequence, speed, delta)
        if point is None:
            invalid += 1
            previous = None
        else:
            valid += 1
            location = (point.latitude, point.longitude)
            if location == previous:
                duplicates += 1
            previous = location
            if point_offset <= sequence < point_offset + max_points:
                result.append(point)
        sequence += 1
        offset = end + 1
    kind = (
        GeometryKind.INVALID
        if valid == 0
        else GeometryKind.SINGLE_POINT
        if valid == 1
        else GeometryKind.SAMPLED_UNVERIFIED
    )
    quality = TrackQuality(
        kind=kind,
        field_state=FieldState.VALID if valid else FieldState.INVALID,
        raw_point_count=sequence,
        valid_point_count=valid,
        invalid_point_count=invalid,
        consecutive_duplicate_count=duplicates,
        scan_complete=True,
    )
    return tuple(result), quality, ("invalid_track_points",) if invalid else ()


def parse_track(
    value: object, max_points: int
) -> tuple[tuple[RideTrackPoint, ...], int | None, tuple[str, ...]]:
    """Compatibility projection; quality callers use scan_track instead."""
    points, quality, issues = scan_track(value, max_points)
    return points, 0 if value is None else quality.raw_point_count, issues


def field_state(item: dict[str, Any], keys: tuple[str, ...], value: object) -> FieldState:
    """Classify only verified protocol paths, without retaining arbitrary keys."""
    if value is not None:
        return FieldState.VALID
    present = [item[key] for key in keys if key in item]
    if not present:
        return FieldState.MISSING
    if all(value is None for value in present):
        return FieldState.NULL
    return FieldState.INVALID


def parse_ride(
    raw: object,
    month: str,
    *,
    source: Endpoint = Endpoint.TRAVEL,
    max_points: int = 500,
    point_offset: int = 0,
) -> Ride:
    previous_month(month)
    item = payload(raw)
    issues: list[str] = []
    provenance: dict[str, str] = {}
    travel_id, legacy_id = opaque_id(item.get("travel_id")), opaque_id(item.get("id"))
    explicit_detail = opaque_id(item.get("detail_id"))
    ride_id = travel_id or legacy_id or explicit_detail
    # travel_id is the cloud detail request parameter, confirmed by recon and
    # a real same-trip query. Legacy id alone has no proven detail equivalence.
    detail_id = explicit_detail or travel_id
    if travel_id:
        provenance["ride_id"] = "travel_id"
        provenance["detail_id"] = "travel_id:verified-detail-parameter"
    elif ride_id:
        provenance["ride_id"] = "legacy-id" if legacy_id else "detail_id"
    if travel_id and legacy_id and travel_id != legacy_id:
        detail_id = None
        issues.append("conflicting_ride_ids")
    if explicit_detail and travel_id and explicit_detail != travel_id:
        detail_id = None
        issues.append("conflicting_detail_ids")
    start, end = None, None
    for field, label in (("start_time", "started_at"), ("end_time", "ended_at")):
        value = timestamp(item.get(field))
        formatted = timestamp(item.get(f"{field}_format"), formatted=True)
        if value is not None:
            provenance[label] = f"{field}:unix-seconds"
            if formatted is not None and formatted != value:
                issues.append("conflicting_time_representations")
        elif formatted is not None:
            value = formatted
            provenance[label] = f"{field}_format:Asia/Shanghai"
        elif item.get(field) is not None or item.get(f"{field}_format") is not None:
            issues.append("invalid_timestamp")
        if field == "start_time":
            start = value
        else:
            end = value
    if start and end and end < start:
        start = end = None
        provenance.pop("started_at", None)
        provenance.pop("ended_at", None)
        issues.append("reversed_timestamps")
    distance = number(item.get("mileages"), 0, 100000)
    duration = number(item.get("duration"), 0, 2678400)
    maximum = number(item.get("speed"), 0, 500)
    if distance is not None:
        provenance["distance_m"] = "mileages:km:ninecli-display-contract"
    if duration is not None:
        provenance["duration_s"] = "duration:seconds:recorded-time-difference"
        if start and end and abs((end - start).total_seconds() - duration) > 1:
            issues.append("duration_time_difference")
    if maximum is not None:
        provenance["server_max_speed_m_s"] = "speed:max-km/h:ninecli-display-contract"
    track, quality, track_issues = (
        scan_track(item.get("trail"), max_points, point_offset)
        if source is Endpoint.TRIP_DETAIL and "trail" in item
        else ((), TrackQuality(), ())
    )
    simple = item.get("is_show_simple_point")
    window = item.get("show_simple_point_days")
    simple = simple if type(simple) is bool else None
    window = window if type(window) is int and 0 <= window <= 36500 else None
    quality = replace(
        quality,
        server_simplified=simple,
        server_window_days=window,
        simplified_state=field_state(item, ("is_show_simple_point",), simple),
        window_state=field_state(item, ("show_simple_point_days",), window),
        kind=GeometryKind.SERVER_SIMPLIFIED
        if simple is True and quality.valid_point_count
        else quality.kind,
    )
    total = quality.raw_point_count
    issues.extend(track_issues)
    samples = tuple(
        SpeedSample(point.sequence, point.speed_raw)
        for point in track
        if point.speed_raw is not None
    )
    energy = number(item.get("ec"), 0)
    used = number(item.get("used_electricity"), 0)
    average = number(item.get("avg_speed"), 0)
    for name, metric_value, path in (
        ("energy_raw", energy, "ec:Wh:maintainer-confirmed"),
        ("used_electricity_raw", used, "used_electricity:unit-unknown"),
        ("speed_raw", maximum, "speed:max-km/h:ninecli-display-contract"),
        ("server_average_speed_raw", average, "avg_speed:semantics-unknown"),
    ):
        if metric_value is not None:
            provenance[name] = path
    states = {
        name: field_state(item, paths, value)
        for name, paths, value in (
            ("started_at", ("start_time", "start_time_format"), start),
            ("ended_at", ("end_time", "end_time_format"), end),
            ("distance_m", ("mileages",), distance),
            ("duration_s", ("duration",), duration),
            ("energy_raw", ("ec",), energy),
            ("used_electricity_raw", ("used_electricity",), used),
            ("speed_raw", ("speed",), maximum),
            ("server_max_speed_m_s", ("speed",), maximum),
            ("server_average_speed_raw", ("avg_speed",), average),
        )
    }
    # Absent/null trail is unknown, not a proven empty route. Malformed points
    # can still yield a bounded partial track; issues carry its incompleteness.
    trail_state = quality.field_state
    states["track_points"] = trail_state
    if trail_state in (FieldState.VALID, FieldState.EMPTY):
        provenance["track_points"] = "trail:lon-lat-speedRaw-deltaRaw:four-column-string"
    if trail_state in (FieldState.MISSING, FieldState.NULL):
        total = None
    return Ride(
        month,
        source,
        ride_id,
        detail_id,
        start,
        end,
        distance * 1000 if distance is not None else None,
        duration,
        energy,
        used,
        maximum,
        maximum / 3.6 if maximum is not None else None,
        average,
        samples,
        track,
        total,
        total is not None and (point_offset > 0 or total > point_offset + max_points),
        tuple(sorted(set(issues))),
        tuple(sorted(provenance.items())),
        field_states=tuple(sorted(states.items())),
        field_sources=tuple((name, source.value) for name in sorted(provenance)),
        precision=numeric_precision(item, ("mileages", "duration", "ec", "speed")),
        track_quality=quality,
        track_offset=point_offset,
        track_limit=max_points,
    )


def parse_rides(raw: object, month: str) -> tuple[Ride, ...]:
    previous_month(month)
    item = payload(raw)
    reported = text(item.get("month"))
    if reported is not None and reported != month:
        raise NinebotError(ErrorKind.PROTOCOL)
    rows = item.get("list")
    if rows is None:
        return ()
    if not isinstance(rows, list) or len(rows) > MAX_RIDES:
        raise NinebotError(ErrorKind.PROTOCOL)
    # No arbitrary track aliases or detail fanout in periodic month polling.
    return tuple(parse_ride(row, month) for row in rows)


def latest_ride(rides: tuple[Ride, ...]) -> Ride | None:
    candidates = [ride for ride in rides if ride.ended_at or ride.started_at]
    return (
        max(candidates, key=lambda ride: (ride.ended_at or ride.started_at, ride.ride_id or ""))
        if candidates
        else None
    )


def select_geometry(previous: Ride, candidate: Ride) -> tuple[bool, str]:
    """Conservative selection without pretending point counts prove truth.

    Fewer points without an upstream revision may be legitimate correction;
    preserve the candidate for future reconciliation, but do not erase evidence.
    """
    old, new = previous.track_quality, candidate.track_quality
    if not previous.track_points:
        return bool(candidate.track_points) or new.field_state is FieldState.EMPTY, "initial"
    if not candidate.track_points:
        return False, "retained_previous_missing_geometry"
    if new.server_simplified is True and old.server_simplified is not True:
        return False, "retained_previous_server_simplified"
    if new.scan_complete and (
        (new.invalid_point_count or 0) > (old.invalid_point_count or 0)
        or (new.consecutive_duplicate_count or 0) > (old.consecutive_duplicate_count or 0)
    ):
        return False, "retained_previous_geometry_conflict"
    if (
        old.scan_complete
        and new.scan_complete
        and (
            (new.valid_point_count or 0) < (old.valid_point_count or 0)
            and old.server_simplified is not True
        )
    ):
        return False, "retained_previous_unverified_reduction"
    return True, "accepted_candidate"


def merge_detail(summary: Ride, detail: Ride) -> Ride:
    """Supplement known values; missing/null/invalid detail cannot erase them."""
    if summary.query_month != detail.query_month:
        raise NinebotError(ErrorKind.PROTOCOL)
    if summary.ride_id and detail.ride_id and summary.ride_id != detail.ride_id:
        raise NinebotError(ErrorKind.PROTOCOL)
    if summary.detail_id and detail.detail_id and summary.detail_id != detail.detail_id:
        raise NinebotError(ErrorKind.PROTOCOL)
    if {"conflicting_ride_ids", "conflicting_detail_ids"} & set(detail.issues):
        raise NinebotError(ErrorKind.PROTOCOL)
    fields = (
        "started_at",
        "ended_at",
        "distance_m",
        "duration_s",
        "energy_raw",
        "used_electricity_raw",
        "speed_raw",
        "server_max_speed_m_s",
        "server_average_speed_raw",
    )
    updates: dict[str, Any] = {}
    issues = set(summary.issues) | set(detail.issues)
    provenance = dict(summary.field_provenance)
    sources = dict(summary.field_sources)
    states = dict(summary.field_states)
    detail_states = dict(detail.field_states)
    detail_provenance = dict(detail.field_provenance)
    precision = dict(summary.precision)
    detail_precision = dict(detail.precision)

    def accept(field: str) -> None:
        if field in detail_provenance:
            provenance[field] = detail_provenance[field]
        else:
            provenance.pop(field, None)
        sources[field] = detail.source.value
        states[field] = detail_states.get(field, FieldState.VALID)

    for field in fields:
        before, after = getattr(summary, field), getattr(detail, field)
        if after is not None:
            updates[field] = after
            accept(field)
            raw_key = {
                "distance_m": "mileages",
                "duration_s": "duration",
                "energy_raw": "ec",
                "speed_raw": "speed",
            }.get(field)
            if raw_key and raw_key in detail_precision:
                precision[raw_key] = (
                    max(precision.get(raw_key, 0), detail_precision[raw_key])
                    if before == after
                    else detail_precision[raw_key]
                )
            if before is not None and before != after:
                issues.add(f"detail_corrected_{field}")
        elif before is None:
            states[field] = detail_states.get(field, FieldState.MISSING)
    track_state = detail_states.get("track_points")
    use_geometry, selection = select_geometry(summary, detail)
    if use_geometry:
        for field in (
            "track_points",
            "speed_samples",
            "total_track_points",
            "track_truncated",
            "track_quality",
            "track_offset",
            "track_limit",
        ):
            updates[field] = getattr(detail, field)
        accept("track_points")
        issues.discard("invalid_track_points")
        if detail.track_quality.invalid_point_count:
            issues.add("invalid_track_points")
    elif not summary.track_points:
        states["track_points"] = track_state or FieldState.MISSING
    updates["geometry_selection"] = selection
    merged = replace(
        summary,
        source=Endpoint.TRIP_DETAIL,
        **updates,
        issues=tuple(sorted(issues)),
        field_provenance=tuple(sorted(provenance.items())),
        field_states=tuple(sorted(states.items())),
        field_sources=tuple(sorted(sources.items())),
        precision=tuple(sorted(precision.items())),
        detail_field_states=detail.field_states,
    )
    if merged.started_at and merged.ended_at:
        if merged.ended_at < merged.started_at:
            raise NinebotError(ErrorKind.PROTOCOL)
        issues.discard("duration_time_difference")
        if (
            merged.duration_s is not None
            and abs((merged.ended_at - merged.started_at).total_seconds() - merged.duration_s) > 1
        ):
            issues.add("duration_time_difference")
    return replace(merged, issues=tuple(sorted(issues)))
