"""Durable history sync Action, separate from runtime pagination cursors."""

import re
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .compat import validation as vol
from .month_tasks import MAX_SELECTED_MONTHS, progress
from .preservation import CONF_BUDGET, CONF_PRESERVE, DEFAULT_BUDGET
from .ride_archive import ArchiveError, ArchiveFailure
from .services import assert_query_scope, query_month, resolve_vehicle, validation_error
from .sync_job import public_progress


def sync_job_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{32}", value):
        raise vol.Invalid("Invalid sync identity")
    return value


def selected_months(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SELECTED_MONTHS:
        raise vol.Invalid("Select 1–12 months")
    months = tuple(query_month(month) for month in value)
    if len(set(months)) != len(months):
        raise vol.Invalid("Duplicate month")
    return months


SYNC_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Optional("operation", default="start"): vol.In(
            (
                "start",
                "continue",
                "status",
                "cancel",
                "repair",
                "repair_continue",
                "repair_status",
                "repair_cancel",
                "pause",
                "resume",
                "sync_status",
                "synchronize",
                "verify",
                "verify_continue",
                "verify_status",
            )
        ),
        vol.Optional("start_month"): query_month,
        vol.Optional("end_month"): query_month,
        vol.Optional("job_id"): sync_job_id,
        vol.Optional("months"): selected_months,
        vol.Optional("force", default=False): bool,
    }
)


async def async_sync_history(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    co = entry.runtime_data.coordinator
    manager = co.history_sync
    operation = call.data["operation"]
    budget = (
        entry.options.get(CONF_BUDGET, DEFAULT_BUDGET)
        if entry.options.get(CONF_PRESERVE) is True
        else None
    )
    if budget is not None and (type(budget) is not int or not 1 <= budget <= 60):
        budget = DEFAULT_BUDGET
    try:
        if operation.startswith("verify"):
            return await async_verify_history(hass, call)
        if operation in {"pause", "resume", "sync_status", "synchronize"}:
            return await async_history_control(hass, call)
        if operation.startswith("repair"):
            return await async_sparse_history(hass, call)
        if operation == "start":
            if "start_month" not in call.data or "end_month" not in call.data:
                raise validation_error("history_range")
            job = await manager.async_start(sn, call.data["start_month"], call.data["end_month"])
            job = await manager.async_advance(sn, job.job_id, budget=budget)
        else:
            if operation != "status" and "job_id" not in call.data:
                raise validation_error("sync_job")
            job = await manager.async_job(sn, call.data.get("job_id"))
            if operation == "continue":
                job = await manager.async_advance(sn, job.job_id, budget=budget)
            elif operation == "cancel":
                job = await manager.async_cancel(sn, job.job_id)
    except ValueError as err:
        raise validation_error("history_range") from err
    except ArchiveError as err:
        co.statistics.record_error(err)
        raise validation_error("statistics_unavailable") from err
    assert_query_scope(hass, call.data["device_id"], entry.entry_id, sn, False, require_live=False)
    return {"schema_version": 1, "operation": operation, **public_progress(job, dt_util.utcnow())}


async def async_history_control(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    from dataclasses import asdict

    from .history_plan import month_count

    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    co = entry.runtime_data.coordinator
    runner, archive = co.preservation.bootstrap, co.statistics.archive
    operation = call.data["operation"]
    performed = False
    if operation in {"resume", "synchronize"}:
        await co.statistics.async_recheck_capacity(
            runner.guard(sn, require_enabled=False, respect_pause=False)
        )
    if operation == "synchronize" and entry.options.get(CONF_PRESERVE) is True:
        from .adapters import month_at
        from .query_broker import Priority

        await runner.async_pause(sn, False)
        await runner.async_request(sn)
        now = dt_util.utcnow()
        await runner.async_seed((sn,), now)
        baseline = await archive.async_history_plan(sn)
        budget = entry.options.get(CONF_BUDGET, DEFAULT_BUDGET)
        if type(budget) is not int or not 1 <= budget <= 60:
            budget = DEFAULT_BUDGET
        selected = call.data.get("months")
        if selected is not None or call.data.get("force"):
            # Explicit months become durable work, including old months and all selections.
            # Repeated clicks join an existing run; page/detail work keeps the shared budget.
            for month in selected or (month_at(now),):
                await archive.async_schedule_history_run(sn, month, now, guard=runner.guard(sn))
            performed = await co.preservation.incremental.async_step((sn,), now, budget)
        elif baseline is not None and not baseline.discovery_complete:
            performed = await runner.async_step((sn,), now, budget)
        else:
            months = call.data.get("months", (month_at(now),))
            if await archive.async_background_budget(sn, now, budget, guard=runner.guard(sn)):
                await co.async_query_month(
                    sn, months[0], priority=Priority.INTERACTIVE, refresh=True
                )
                performed = True
            await co.preservation.incremental.async_seed((sn,), dt_util.utcnow())
    if operation in {"pause", "resume"}:
        await runner.async_pause(sn, operation == "pause")
    await co.preservation.async_refresh_progress((sn,), dt_util.utcnow())
    paused = await archive.async_history_paused(sn)
    plan = await archive.async_history_plan(sn)
    health = await archive.async_history_health(sn, plan.plan_id) if plan else None
    view = co.preservation.progress.get(sn)
    return {
        "schema_version": 1,
        "operation": operation,
        "paused": paused,
        "background_enabled": entry.options.get(CONF_PRESERVE) is True,
        "automatic_sync_enabled": runner.enabled(),
        "manual_sync_requested": await archive.async_history_requested(sn),
        "sync_state": view.state if view else None,
        "scan_progress": view.scan_percent if view else None,
        "detail_progress": view.detail_percent if view else None,
        "last_success": view.last_success.isoformat() if view and view.last_success else None,
        "operation_performed": performed,
        "state": "paused" if paused else plan.state.value if plan else "not_started",
        "scanned_months": plan.scanned_months if plan else None,
        "total_months": month_count(plan.start_month, plan.end_month) if plan else None,
        "verified_months": plan.verified_months if plan else None,
        "gap_months": plan.gap_months if plan else None,
        "discovery_complete": plan.discovery_complete if plan else False,
        "details": asdict(health) if health else None,
        "pagination_basis": "client_page_evidence",
        "real_cloud_pagination_verified": False,
    }


async def async_verify_history(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Explicit bounded local checks; no implicit cloud repair or raw output."""
    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    co = entry.runtime_data.coordinator
    archive = co.statistics.archive
    owns = co.preservation.bootstrap.guard(sn, require_enabled=False, respect_pause=False)
    now, operation = dt_util.utcnow(), call.data["operation"]
    if operation == "verify":
        await archive.async_start_history_audit(sn, now, guard=owns, restart=True)
    if operation != "verify_status":
        await archive.async_step_history_audit(sn, now, guard=owns)
    plan, current_revision, inventory_revision = await archive.async_history_audit_status(sn)
    changed = bool(
        plan
        and (
            plan.changed
            or plan.cutoff_revision != current_revision
            or plan.cutoff_inventory != inventory_revision
        )
    )
    co.preservation.audit.publish(sn, plan, owns)
    if plan and plan.invalid:
        co.statistics.record_error(ArchiveError(ArchiveFailure.OBJECT))
    return {
        "schema_version": 1,
        "contract": "local_payload_integrity_v1",
        "operation": operation,
        "state": plan.phase if plan else "not_started",
        "captured_total": plan.total if plan else None,
        "checked": plan.checked if plan else 0,
        "invalid": plan.invalid if plan else 0,
        "archive_changed": changed,
        "payloads_verified": bool(plan and plan.payloads_verified and not changed),
        "captured_payloads_verified": plan.payloads_verified if plan else False,
        "cutoff_revision": plan.cutoff_revision if plan else None,
        "current_revision": current_revision,
        "cutoff_inventory": plan.cutoff_inventory if plan else None,
        "current_inventory": inventory_revision,
        "updated_at": plan.updated_at if plan else None,
        "cloud_requests": 0,
        "normalization_and_cloud_coverage_verified": False,
    }


async def async_sparse_history(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    co = entry.runtime_data.coordinator
    if not co.statistics.archive_ready:
        raise validation_error("statistics_unavailable")
    manager, archive = co.preservation.months, co.statistics.archive
    operation = call.data["operation"]
    months = call.data.get("months")
    budget = entry.options.get(CONF_BUDGET, DEFAULT_BUDGET)
    if type(budget) is not int or not 1 <= budget <= 60:
        budget = DEFAULT_BUDGET
    performed = False
    if operation == "repair":
        if months is None:
            raise validation_error("history_range")
        await archive.async_schedule_months(sn, months, dt_util.utcnow(), guard=manager.guard(sn))
        performed = await manager.async_step(sn, budget, dt_util.utcnow(), months=months)
    elif operation == "repair_continue":
        performed = await manager.async_step(sn, budget, dt_util.utcnow(), months=months)
    elif operation == "repair_cancel":
        if months is None:
            raise validation_error("history_range")
        await manager.async_cancel(sn, months)
    tasks = await archive.async_month_tasks(sn)
    if months is not None:
        tasks = tuple(task for task in tasks if task.month in months)
    return {
        "schema_version": 1,
        "operation": operation,
        "tasks": progress(tasks, dt_util.utcnow()),
        "operation_performed": performed,
        "background_enabled": entry.options.get(CONF_PRESERVE) is True,
        "upstream_pagination_verified": False,
        "coverage_note": "A successful month read may remain partial; no cloud page support.",
    }
