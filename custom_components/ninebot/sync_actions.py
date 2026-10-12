"""Durable history sync Action, separate from runtime pagination cursors."""

import re
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .compat import validation as vol
from .month_tasks import MAX_SELECTED_MONTHS, progress
from .preservation import CONF_BUDGET, CONF_PRESERVE, DEFAULT_BUDGET
from .ride_archive import ArchiveError
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
            )
        ),
        vol.Optional("start_month"): query_month,
        vol.Optional("end_month"): query_month,
        vol.Optional("job_id"): sync_job_id,
        vol.Optional("months"): selected_months,
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
