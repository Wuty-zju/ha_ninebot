"""Small reviewed Assistant projections; never forward an Action response wholesale."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .archive_timeline import SELECTION_BASIS
from .exceptions import Endpoint as FailureEndpoint
from .exceptions import EnvelopeCode, ErrorKind
from .models import VehicleSnapshot
from .parsing import boolean
from .polling_policy import DAY_OBSERVATION_MAX_AGE
from .ride_models import GeometryKind
from .travel import opaque_id

METRIC_UNITS = {
    "distance_km": "km",
    "ride_count": None,
    "duration_s": "s",
    "energy_wh": "Wh",
    "energy_intensity_wh_per_km": "Wh/km",
}
# Label, native unit and observation group for a single cached reading.
STATUS_READINGS = {
    "battery_soc_percent": ("Main battery charge", "%", "status"),
    "charging": ("Charging", None, "status"),
    "powered": ("Power on", None, "status"),
    "vehicle_locked": ("Vehicle locked", None, "status"),
    "seat_locked": ("Seat latch locked", None, "status"),
    "acc_active": ("Accessory power on", None, "status"),
    "precise_range_km": ("Precise range", "km", "status"),
    "estimated_range_km": ("Estimated range", "km", "status"),
    "ai_range_km": ("AI estimated range", "km", "status"),
    "emergency_soc_percent": ("Communication battery charge", "%", "battery"),
    "charging_power_w": ("Charging power", "W", "battery"),
}
RIDE_UNITS = {
    "distance_m": "m",
    "duration_s": "s",
    "energy_wh": "Wh",
    "max_speed_m_s": "m/s",
    "average_speed_m_s": "m/s",
    "energy_intensity_wh_per_km": "Wh/km",
}
REASONS = {
    "adjacent_month_not_observed",
    "available",
    "not_reported",
    "missing_month",
    "day_not_observed",
    "missing_daily_chart",
    "incomplete_month_list",
    "missing_metric",
    "zero_distance",
}


@dataclass
class AssistantView:
    data: dict[str, Any]
    quality: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def numeric(value: object) -> int | float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def flag(value: object) -> bool | None:
    return value if type(value) is bool else None


def choice(value: object, allowed: set[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def list_count(value: object) -> int | None:
    return len(value) if isinstance(value, list) else None


def mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def safe_label(value: str, *identifiers: str) -> str:
    """Vehicle labels are consented; account titles and serials are not."""
    for identifier in identifiers:
        if identifier:
            value = value.replace(identifier, "")
    value = re.sub(r"[0-9]{7,}", "", value)
    return "".join(char for char in value if char.isprintable()).strip()[:96] or "Vehicle"


def query_failure_view(value: object) -> dict[str, Any] | None:
    """A local failure fact, never an exception, log line or whole diagnostic."""
    row = mapping(value)
    kind = choice(row.get("error_kind"), {kind.value for kind in ErrorKind}, "unknown")
    if kind == "unknown":
        return None
    status = row.get("proxy_http_status")
    return {
        "error_kind": kind,
        "stage": choice(row.get("stage"), {"request", "normalize", "archive"}, "unknown"),
        "endpoint": choice(row.get("endpoint"), {e.value for e in FailureEndpoint}, "unknown"),
        "proxy_http_status": status if type(status) is int and 100 <= status <= 599 else None,
        "envelope_code": choice(
            row.get("envelope_code"), {code.value for code in EnvelopeCode}, "other"
        )
        if row.get("envelope_code") is not None
        else None,
        "attempted_at": timestamp(row.get("attempted_at")),
        "retryable": flag(row.get("retryable")),
    }


def status_view(
    snapshot: VehicleSnapshot,
    fresh: dict[str, bool],
    failures: dict[str, Any] | None = None,
    *,
    metric: str | None = None,
) -> AssistantView:
    status, battery = snapshot.status, snapshot.battery
    seat = boolean(status.observations.get("seat_lock_raw"))
    values = {
        "battery_soc_percent": numeric(status.battery),
        "charging": flag(status.charging),
        "powered": flag(status.powered),
        "vehicle_locked": flag(status.locked),
        "seat_locked": None if seat is None else not seat,
        "acc_active": boolean(status.observations.get("acc_raw")),
        "precise_range_km": numeric(status.range_precise),
        "estimated_range_km": numeric(status.range_estimated),
        "ai_range_km": numeric(status.range_ai),
        "emergency_soc_percent": numeric(battery.emergency_soc),
        "charging_power_w": numeric(battery.charging_power_raw),
    }
    groups = {}
    for group in fresh:
        stamp = getattr(snapshot, f"{group}_freshness")
        groups[group] = {
            "status": "available" if fresh[group] else "stale" if stamp.succeeded_at else "missing",
            "received_at": timestamp(stamp.succeeded_at),
            "last_attempt_at": timestamp(stamp.attempted_at),
            "error_kind": stamp.error.value if stamp.error else None,
            "failure_context": query_failure_view(failures.get(group)) if failures else None,
        }
    readings = values if metric is None else {metric: values[metric]}
    metrics = {
        key: {"status": "missing" if value is None else groups[STATUS_READINGS[key][2]]["status"]}
        for key, value in readings.items()
    }
    if metric is not None:
        return AssistantView(
            data={**readings, "units": {metric: STATUS_READINGS[metric][1]}},
            quality={"groups": groups, "metrics": metrics},
            evidence={"source_kind": "runtime_snapshot"},
            warnings=["cached_values_are_not_a_cloud_refresh"]
            + (["acc_does_not_prove_motion"] if metric == "acc_active" else [])
            + (["seat_state_is_latch_only"] if metric == "seat_locked" else []),
        )
    return AssistantView(
        data={
            **values,
            "units": {
                **{key: unit for key, (_, unit, _) in STATUS_READINGS.items() if unit},
                "voltage_v": "V",
                "temperature_c": "°C",
                "cycles": None,
            },
            "cached_pack_count": len(battery.batteries),
            "packs": [
                {
                    "index": index,
                    "voltage_v": numeric(pack.voltage),
                    "temperature_c": numeric(pack.temperature),
                    "cycles": numeric(pack.cycles),
                    "cycle_supported": flag(pack.cycle_supported),
                }
                for index, pack in enumerate(battery.batteries[:4], 1)
            ],
            "more_packs_available": len(battery.batteries) > 4,
        },
        quality={"groups": groups, "metrics": metrics},
        evidence={
            "source_kind": "runtime_snapshot",
            "seat_state_basis": "lock_latch_only",
            "failure_context_basis": "local_query_stage_and_proxy_response",
        },
        warnings=["acc_does_not_prove_motion", "cached_values_are_not_a_cloud_refresh"],
    )


def statistics_period_complete(quality: dict[str, Any], source: object) -> bool:
    """Closing a day needs its ride window; closing a month needs a later observation."""
    if quality.get("period_closed") is not True:
        return False
    if source == "account_archive":
        return quality.get("ride_window_complete") is True
    return source == "server_month_summary" and quality.get("observed_after_period_end") is True


def statistics_view(
    row: dict[str, Any], *, daily: bool, now: datetime, metric: str | None = None
) -> AssistantView:
    units = METRIC_UNITS if metric is None else {metric: METRIC_UNITS[metric]}
    values = {name: numeric(row.get(name)) for name in units}
    received = timestamp(row.get("received_at"))
    availability = mapping(row.get("availability"))
    old_open_observation = bool(
        row.get("period_closed") is not True
        and received
        and (now - datetime.fromisoformat(received)).total_seconds() > DAY_OBSERVATION_MAX_AGE
    )
    provisional_closed_month = bool(
        not daily
        and row.get("period_closed") is True
        and row.get("observed_after_period_end") is not True
    )
    metrics = {}
    for name, value in values.items():
        reason = availability.get(name, "available" if value is not None else "not_reported")
        metrics[name] = {
            "status": "missing"
            if value is None
            else "stale"
            if old_open_observation
            else "partial"
            if provisional_closed_month
            else "available",
            "reason": choice(reason, REASONS, "unknown_reason"),
        }
    return AssistantView(
        data={**values, "units": units},
        quality={
            "metrics": metrics,
            "received_at": received,
            "period_closed": flag(row.get("period_closed")),
            "ride_window_complete": flag(row.get("ride_window_complete")) if daily else None,
            "observed_after_period_end": flag(row.get("observed_after_period_end")),
            "adjacent_received_at": timestamp(row.get("adjacent_received_at")) if daily else None,
            "completeness_basis": choice(
                row.get("completeness_basis"),
                {"upstream_complete", "local_reconciled", "incomplete"},
                "unknown",
            )
            if daily
            else "server_month_summary",
        },
        evidence={
            "source_kind": "account_archive" if daily else "server_month_summary",
            "coverage_basis": "business_day_projection" if daily else "server_month_summary",
            "revision": numeric(row.get("revision")),
            "distance_basis": choice(
                row.get("distance_basis"),
                {"server_daily_chart", "returned_unique_rides"},
                "server_month_summary" if not daily else "unknown",
            ),
            "ride_date_assignment": "whole_ride_end_business_date" if daily else "server_month",
            "energy_intensity_basis": "returned_unique_rides" if daily else "server_month_summary",
        },
        warnings=["local_observation_only"]
        + (["server_daily_distance_and_ride_end_date_can_differ"] if daily else []),
    )


def ride_view(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only scalar ride metrics; no raw units, samples, GPS or source-key paths."""
    return {
        "ride_id": opaque_id(row.get("ride_id")),
        "query_month": row.get("query_month")
        if isinstance(row.get("query_month"), str)
        and re.fullmatch(r"20[0-9]{2}(0[1-9]|1[0-2])", row["query_month"])
        else None,
        "started_at": timestamp(row.get("start_time")),
        "ended_at": timestamp(row.get("end_time")),
        **{name: numeric(row.get(name)) for name in RIDE_UNITS},
        "detail_saved": flag(row.get("detail_saved")),
    }


def trips_view(response: dict[str, Any], rides: list[dict[str, Any]]) -> AssistantView:
    coverage = mapping(response.get("coverage"))
    cursor = response.get("next_cursor")
    month_rows = response.get("months")
    months = [
        {
            "month": row["month"],
            "received_at": timestamp(row.get("received_at")),
            "observed_list_complete": flag(row.get("observed_list_complete")),
        }
        for row in (month_rows[:3] if isinstance(month_rows, list) else [])
        if isinstance(row, dict)
        and isinstance(row.get("month"), str)
        and re.fullmatch(r"20[0-9]{2}(0[1-9]|1[0-2])", row["month"])
    ]
    return AssistantView(
        data={
            "rides": [
                {**ride_view(row), "received_at": timestamp(row.get("received_at"))}
                for row in rides
            ],
            "units": RIDE_UNITS,
            "returned": len(rides),
            "has_more": cursor is not None,
            "next_cursor": cursor,
        },
        quality={
            "page_as_of": timestamp(mapping(response.get("range")).get("as_of")),
            "months": months,
            "all_observed_lists_complete": flag(coverage.get("all_observed_lists_complete")),
            "upstream_history_complete": "unverified",
            "missing_month_count": list_count(coverage.get("missing_months")),
            "partial_month_count": list_count(coverage.get("partial_months")),
            "unknown_month_count": list_count(coverage.get("unknown_months")),
        },
        evidence={
            "source_kind": "account_archive",
            "revision": numeric(response.get("revision")),
            "selection_basis": SELECTION_BASIS,
            "sort": "desc",
        },
        warnings=[
            "returned_page_is_not_a_period_total",
            "local_coverage_does_not_prove_cloud_total",
        ],
    )


def detail_view(response: dict[str, Any]) -> AssistantView:
    row, context = mapping(response.get("ride")), mapping(response.get("detail_context"))
    track, integrity = mapping(row.get("track_summary")), mapping(context.get("local_integrity"))
    geometry = track.get("geometry_kind")
    return AssistantView(
        data={**ride_view(row), "units": RIDE_UNITS},
        quality={
            "received_at": timestamp(response.get("received_at")),
            "partial_payload": flag(integrity.get("partial")),
            "geometry_kind": choice(geometry, {kind.value for kind in GeometryKind}, "unknown"),
            "valid_points": numeric(track.get("valid_points")),
            "invalid_points": numeric(track.get("invalid_points")),
            "scan_complete": flag(track.get("scan_complete")),
            "server_simplified": flag(track.get("server_simplified")),
        },
        evidence={
            "source_kind": "account_archive",
            "revision": numeric(context.get("archive_revision")),
        },
        warnings=["track_and_speed_samples_not_included"],
    )


def archive_counts_view(value: object) -> dict[str, int] | None:
    """Validate the aggregate and its subsets together before exposing it."""
    row = mapping(value)
    fields = ("total", "saved_details", "simplified", "details_pending_classification")
    counts: dict[str, int] = {}
    for name in fields:
        count = row.get(name)
        if type(count) is not int or not 0 <= count < 2**63:
            return None
        counts[name] = count
    simplified_or_pending = counts["simplified"] + counts["details_pending_classification"]
    if not simplified_or_pending <= counts["saved_details"] <= counts["total"]:
        return None
    return counts


def sync_view(response: dict[str, Any]) -> AssistantView:
    # These are the public states emitted by HistoryProgress/HistoryState.
    states = {
        "not_started",
        "disabled",
        "paused",
        "discovering",
        "preserving",
        "verifying",
        "incremental",
        "waiting_budget",
        "waiting_retry",
        "waiting_connection",
        "vehicle_not_present",
        "needs_login",
        "storage_full",
        "storage_error",
        "complete",
        "with_gaps",
        "tracks_limited",
        "reconciled",
        "idle",
        "querying",
        "discover",
        "preserve",
        "verify",
        "synced",
    }
    health = mapping(response.get("details"))
    return AssistantView(
        data={
            "state": choice(response.get("sync_state") or response.get("state"), states, "unknown"),
            "phase": choice(
                response.get("sync_phase"), {"discovering", "preserving", "incremental"}, "unknown"
            ),
            "paused": flag(response.get("paused")),
            "background_enabled": flag(response.get("background_enabled")),
            "automatic_sync_enabled": flag(response.get("automatic_sync_enabled")),
            **{
                name: numeric(response.get(name))
                for name in (
                    "scanned_months",
                    "total_months",
                    "verified_months",
                    "gap_months",
                    "scan_progress",
                    "detail_progress",
                )
            },
            **{
                name: timestamp(response.get(name))
                for name in ("last_success", "last_completed", "next_request_at", "retry_at")
            },
            "details": {
                name: numeric(health.get(name))
                for name in (
                    "discovered",
                    "saved",
                    "missing",
                    "unmapped",
                    "quality_pending",
                    "simplified",
                    "single_point",
                    "unavailable_track",
                    "latest_degraded",
                    "reference_issues",
                )
            },
            "archive_counts": archive_counts_view(response.get("archive_counts")),
            "discovery_complete": flag(response.get("discovery_complete")),
        },
        quality={"upstream_history_complete": "unverified"},
        evidence={
            "source_kind": "account_archive",
            "operation": "sync_status",
            "archive_counts_scope": "all_saved_vehicle_trips",
            "details_scope": "discovered_history_plan",
        },
        warnings=["budget_wait_is_local_scheduling_not_api_permission"],
    )
