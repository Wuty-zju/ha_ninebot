"""A strict diagnostic whitelist. No raw payload, identity or position."""

import platform
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from itertools import islice
from typing import TYPE_CHECKING, Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import adapters
from .battery import battery_summary
from .capabilities import CONTROL_ACTIONS
from .compat import child_registry_api_available
from .exceptions import NinebotError
from .history_entities import HISTORY_STATES
from .history_plan import HistoryPlan, month_count
from .models import TravelMonth
from .month_summary import MonthSummary
from .parsing import integer, number, previous_month
from .raw import Endpoint, RawRecord
from .ride_models import FieldState
from .runtime import NinebotConfigEntry
from .statistics_store import StoredMonth, timestamp

if TYPE_CHECKING:
    from .preservation import HistoryPreservation

MAX_DIAGNOSTIC_VEHICLES = 32
MONTH_WARNINGS = frozenset(
    {
        "daily_mileage_invalid",
        "daily_mileage_inconsistent",
        "daily_mileage_total_unavailable",
        "month_list_partial",
        "reported_count_inconsistent",
        "reported_count_unavailable",
        "ride_identity_incomplete",
    }
)


def _month_evidence(
    month: str,
    record: RawRecord | None,
    travel: TravelMonth | None,
    succeeded_at: datetime | None,
    stored: StoredMonth | None,
) -> dict[str, Any]:
    """Project two bounded cache owners off-loop; never return raw values or IDs."""
    summary: MonthSummary | None = travel.summary if travel and travel.month == month else None
    source = "snapshot" if summary else "unobserved"
    received = succeeded_at.isoformat() if summary and succeeded_at else None
    raw_list: dict[str, Any] = {
        "state": "unobserved",
        "source": "unobserved",
        "received_at": None,
        "server_count": None,
        "item_count": None,
        "parse_status": "unobserved",
    }
    if record is not None:
        raw_list.update(
            source="raw_cache", received_at=record.received_at.isoformat(), parse_status="invalid"
        )
        raw_list["state"] = FieldState.INVALID.value
        raw = record.payload()
        if isinstance(raw, dict):
            rows = raw.get("list")
            raw_list["server_count"] = integer(raw.get("times"))
            raw_list["state"] = (
                FieldState.MISSING
                if "list" not in raw
                else FieldState.NULL
                if rows is None
                else FieldState.EMPTY
                if isinstance(rows, list) and not rows
                else FieldState.VALID
                if isinstance(rows, list)
                else FieldState.INVALID
            ).value
            raw_list["item_count"] = len(rows) if isinstance(rows, list) else None
        try:
            # The snapshot owns this exact successful response; reuse its summary.
            parsed = (
                travel
                if summary is not None and succeeded_at == record.received_at
                else adapters.travel(raw, month)
            )
        except NinebotError:
            if raw_list["state"] == FieldState.VALID.value:
                raw_list["state"] = FieldState.INVALID.value
        else:
            assert parsed is not None
            summary = parsed.summary
            source, received = "raw_cache", record.received_at.isoformat()
            raw_list["parse_status"] = "accepted"
    counts: dict[str, Any] = {
        "source": source,
        "received_at": received,
        "server_count": integer(summary.ride_count) if summary else None,
        "returned_count": integer(summary.returned_count) if summary else None,
        "unique_count": integer(summary.unique_ride_count) if summary else None,
        "list_complete": summary.list_complete if summary else None,
        "coverage": number(summary.coverage, 0, 1) if summary else None,
        "warnings": sorted(MONTH_WARNINGS.intersection(summary.warnings)) if summary else None,
    }
    if summary is None and stored is not None:
        # This projection does not retain row counts/coverage/warnings. Do not
        # reconstruct them from deduplicated IDs or mistake eviction for zero.
        counts.update(
            source="statistics_projection",
            received_at=timestamp(stored.received_at).isoformat(),
            server_count=integer(stored.ride_count),
            list_complete=stored.list_complete,
        )
    return {"month": month, "summary": counts, "raw_list": raw_list}


def _scan_scope(plan: HistoryPlan | None) -> dict[str, Any]:
    if plan is None:
        return {"source": "unobserved", "start_month": None, "end_month": None, "next_month": None}
    month_count(plan.start_month, plan.end_month)
    return {
        "source": "bootstrap_cache",
        "start_month": plan.start_month,
        "end_month": plan.end_month,
        "next_month": plan.next_month,
    }


def _history_evidence(preservation: "HistoryPreservation", sn: str) -> dict[str, Any]:
    progress = preservation.progress.get(sn)
    baseline = preservation.bootstrap.snapshots.get(sn)
    incremental = preservation.bootstrap.incremental_snapshots.get(sn)
    result: dict[str, Any] = {
        "source": "preservation.progress" if progress else "unobserved",
        "state": "unknown",
        "baseline_scope": _scan_scope(baseline[0] if baseline else None),
        "incremental_scope": _scan_scope(incremental[0] if incremental else None),
    }
    for key in (
        "scanned_months",
        "verified_months",
        "total_months",
        "gap_months",
        "discovered",
        "saved",
        "missing",
        "unmapped",
        "quality_pending",
        "simplified",
        "active_months",
        "multi_page_months",
        "reference_issues",
        "initial_gap_months",
        "resolved_gap_months",
    ):
        result[key] = integer(getattr(progress, key)) if progress else None
    result.update(
        state=progress.state if progress and progress.state in HISTORY_STATES else "unknown",
        phase=progress.phase
        if progress and progress.phase in {"discovering", "preserving", "verifying", "incremental"}
        else None,
        discovery_complete=progress.discovery_complete if progress else None,
        scan_percent=(min(100, result["verified_months"] * 100 // result["total_months"]))
        if result["total_months"] and result["verified_months"] is not None
        else None,
        detail_percent=(min(100, result["saved"] * 100 // result["discovered"]))
        if result["discovered"] and result["saved"] is not None
        else 100
        if result["discovered"] == 0
        and result["saved"] == 0
        and progress is not None
        and progress.discovery_complete is True
        else None,
        scan_basis="verified_months/total_months",
        detail_basis="saved/discovered",
    )
    for key in ("last_success", "last_completed", "next_request_at", "retry_at"):
        value = getattr(progress, key) if progress else None
        result[key] = value.isoformat() if value else None
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NinebotConfigEntry
) -> dict[str, Any]:
    try:
        package_version = version("ninecli")
    except PackageNotFoundError:
        package_version = None
    result: dict[str, Any] = {
        "integration_version": str((await async_get_integration(hass, entry.domain)).version),
        "ninecli_version": package_version,
        "ha_version": HA_VERSION,
        "python_version": platform.python_version(),
        "platform": {"system": platform.system(), "architecture": platform.machine()},
        "entry_state": entry.state.value,
        "runtime_available": False,
    }
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return result
    result["runtime_available"] = True
    now = dt_util.utcnow()
    current_month = adapters.month_at(now)
    months = (current_month, previous_month(current_month))
    coordinator = runtime.coordinator
    snapshots = tuple(islice(coordinator.data.values(), MAX_DIAGNOSTIC_VEHICLES))
    query_failure = getattr(coordinator, "query_failure", None)
    vehicles = []
    for vehicle_index, snapshot in enumerate(snapshots, start=1):
        sn = snapshot.profile.sn
        month_evidence = []
        for month in months:
            month_evidence.append(
                await hass.async_add_executor_job(
                    _month_evidence,
                    month,
                    coordinator.raw.get(Endpoint.TRAVEL, sn, month, now=now),
                    snapshot.travel,
                    snapshot.travel_freshness.succeeded_at,
                    coordinator.statistics.month(sn, month),
                )
            )
        groups = {}
        for group in ("profile", "status", "battery", "travel"):
            freshness = getattr(snapshot, f"{group}_freshness")
            groups[group] = {
                "attempted_at": freshness.attempted_at.isoformat()
                if freshness.attempted_at
                else None,
                "succeeded_at": freshness.succeeded_at.isoformat()
                if freshness.succeeded_at
                else None,
                "error": freshness.error.value if freshness.error else None,
                "failure_context": query_failure(sn, group) if callable(query_failure) else None,
            }
        vehicles.append(
            {
                "vehicle_index": vehicle_index,
                "present": snapshot.present,
                "groups": groups,
                "travel_statistics": {"months": month_evidence},
                "history_progress": _history_evidence(coordinator.preservation, sn),
                "status_timing": {
                    "target_interval_seconds": runtime.coordinator.poll_interval(
                        snapshot.profile.sn, "status"
                    ),
                    "last_interval_seconds": runtime.coordinator.status_intervals.get(
                        snapshot.profile.sn
                    ),
                    "deadline_misses": runtime.coordinator.status_deadline_misses.get(
                        snapshot.profile.sn, 0
                    ),
                    "response_age_seconds": max(
                        0,
                        (dt_util.utcnow() - snapshot.status_freshness.succeeded_at).total_seconds(),
                    )
                    if snapshot.status_freshness.succeeded_at
                    else None,
                },
                "polling": runtime.coordinator.polling_diagnostics(snapshot.profile.sn),
                "polling_demand": runtime.coordinator.demand(snapshot.profile.sn).diagnostics(),
                "battery_count": len(snapshot.battery.batteries),
                "battery_model": battery_summary(snapshot.battery),
                "cycle_support": [
                    battery.cycle_supported for battery in snapshot.battery.batteries
                ],
                "controls": {
                    action: runtime.coordinator.controls_enabled(snapshot.profile.sn, action)
                    for action in ("bell", "buck", "engine/start", "engine/stop")
                },
                "control_policy": {
                    action: runtime.coordinator.control_decision(
                        snapshot.profile.sn, action
                    ).diagnostics()
                    for action in sorted(CONTROL_ACTIONS)
                },
                "control_results": runtime.coordinator.control_results.diagnostics(
                    snapshot.profile.sn
                ),
            }
        )
    return {
        **result,
        "compatibility": {"child_registry_api": child_registry_api_available()},
        "backend_support": {
            "query_endpoints": sorted(
                endpoint.value for endpoint in runtime.coordinator.backend.endpoints
            ),
            "control_actions": sorted(runtime.coordinator.backend.control_actions),
        },
        "query_broker": runtime.coordinator.broker.diagnostics(),
        "identity_migration": runtime.identities.diagnostics() if runtime.identities else None,
        "identity_scheme": entry.data.get("identity_scheme"),
        "identity_conflict_count": len(runtime.identity_conflicts),
        "rated_parameters_writable": runtime.models.writable,
        "obsolete_entities_removed": runtime.obsolete_entities_removed,
        "standard_entities_enabled": runtime.standard_entities_enabled,
        "configured_controls_enabled": runtime.configured_controls_enabled,
        "vehicle_discovery": runtime.coordinator.discovery_diagnostics(),
        "diagnostic_limits": {
            "max_vehicles": MAX_DIAGNOSTIC_VEHICLES,
            "vehicle_count": len(coordinator.data),
            "exported_vehicles": len(vehicles),
            "vehicles_truncated": len(coordinator.data) > len(vehicles),
            "vehicle_index_scope": "this_export_only",
            "months_per_vehicle": len(months),
            "month_scope": "current_and_previous_business_month",
            "max_raw_payloads_per_vehicle": len(months),
            "max_month_warnings": len(MONTH_WARNINGS),
            "history_source": "memory_only_no_refresh",
        },
        "vehicles": vehicles,
        "raw_schema": runtime.coordinator.raw.diagnostics(dt_util.utcnow()),
        "ride_events": runtime.events.diagnostics() if runtime.events else None,
        "travel_statistics": {
            "writable": runtime.coordinator.statistics.writable,
            "readable": runtime.coordinator.statistics.available,
            "source_mode": runtime.coordinator.statistics.source_mode,
            "archive_ready": runtime.coordinator.statistics.archive_ready,
            "preservation_status": runtime.coordinator.preservation.status,
            "write_paused": runtime.coordinator.statistics.archive.write_paused,
            "backup_paused": runtime.coordinator.statistics.archive.backup_paused,
            "error_kind": runtime.coordinator.statistics.error.value
            if runtime.coordinator.statistics.error
            else None,
            "restored": runtime.coordinator.statistics.restored,
            "count_scope": "current_and_previous_month_projection",
            "month_count": sum(map(len, runtime.coordinator.statistics.months.values())),
            "ride_metadata_count": sum(map(len, runtime.coordinator.statistics.rides.values())),
        },
    }
