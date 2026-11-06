"""Small reviewed Assistant projections; never forward an Action response wholesale."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .archive_timeline import SELECTION_BASIS
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
    "missing_adjacent_month",
    "missing_metric",
    "invalid_duration",
    "incomplete_ride_metrics",
    "missing_ride_metrics",
    "missing_distance",
    "missing_energy",
    "zero_distance",
    "invalid_value",
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


def status_view(snapshot: VehicleSnapshot, fresh: dict[str, bool]) -> AssistantView:
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
    }
    groups = {}
    for group in ("profile", "status", "battery"):
        stamp = getattr(snapshot, f"{group}_freshness")
        groups[group] = {
            "status": "available" if fresh[group] else "stale" if stamp.succeeded_at else "missing",
            "received_at": timestamp(stamp.succeeded_at),
            "last_attempt_at": timestamp(stamp.attempted_at),
            "error_kind": stamp.error.value if stamp.error else None,
        }
    metrics = {
        key: {"status": "missing" if value is None else groups["status"]["status"]}
        for key, value in values.items()
    }
    metrics.update(
        {
            name: {"status": "missing" if value is None else groups["battery"]["status"]}
            for name, value in (
                ("emergency_soc_percent", numeric(battery.emergency_soc)),
                ("charging_power_w", numeric(battery.charging_power_raw)),
            )
        }
    )
    return AssistantView(
        data={
            **values,
            "units": {
                "battery_soc_percent": "%",
                "precise_range_km": "km",
                "estimated_range_km": "km",
                "ai_range_km": "km",
                "emergency_soc_percent": "%",
                "charging_power_w": "W",
                "voltage_v": "V",
                "temperature_c": "°C",
                "cycles": None,
            },
            "emergency_soc_percent": numeric(battery.emergency_soc),
            "charging_power_w": numeric(battery.charging_power_raw),
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
        evidence={"source_kind": "runtime_snapshot", "seat_state_basis": "lock_latch_only"},
        warnings=["acc_does_not_prove_motion", "cached_values_are_not_a_cloud_refresh"],
    )


def statistics_view(row: dict[str, Any], *, daily: bool, now: datetime) -> AssistantView:
    values = {name: numeric(row.get(name)) for name in METRIC_UNITS}
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
        data={**values, "units": METRIC_UNITS},
        quality={
            "metrics": metrics,
            "received_at": received,
            "period_closed": flag(row.get("period_closed")),
            "ride_window_complete": flag(row.get("ride_window_complete")) if daily else None,
            "observed_after_period_end": flag(row.get("observed_after_period_end")),
            "adjacent_received_at": timestamp(row.get("adjacent_received_at")) if daily else None,
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
            "date_assignment": "whole_ride_end_business_date",
        },
        warnings=["local_observation_only"],
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
    return AssistantView(
        data={
            "rides": [ride_view(row) for row in rides],
            "units": RIDE_UNITS,
            "returned": len(rides),
            "has_more": cursor is not None,
            "next_cursor": cursor,
        },
        quality={
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
        "needs_login",
        "storage_full",
        "storage_error",
        "complete",
        "with_gaps",
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
            "discovery_complete": flag(response.get("discovery_complete")),
        },
        quality={"upstream_history_complete": "unverified"},
        evidence={"source_kind": "account_archive", "operation": "sync_status"},
        warnings=["budget_wait_is_local_scheduling_not_api_permission"],
    )
