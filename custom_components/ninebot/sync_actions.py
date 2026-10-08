"""Durable history sync Action, separate from runtime pagination cursors."""

import re
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .compat import validation as vol
from .ride_archive import ArchiveError
from .services import assert_query_scope, query_month, resolve_vehicle, validation_error
from .sync_job import public_progress


def sync_job_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{32}", value):
        raise vol.Invalid("Invalid sync identity")
    return value


SYNC_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Optional("operation", default="start"): vol.In(
            ("start", "continue", "status", "cancel")
        ),
        vol.Optional("start_month"): query_month,
        vol.Optional("end_month"): query_month,
        vol.Optional("job_id"): sync_job_id,
    }
)


async def async_sync_history(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    co = entry.runtime_data.coordinator
    manager = co.history_sync
    operation = call.data["operation"]
    try:
        if operation == "start":
            if "start_month" not in call.data or "end_month" not in call.data:
                raise validation_error("history_range")
            job = await manager.async_start(sn, call.data["start_month"], call.data["end_month"])
            job = await manager.async_advance(sn, job.job_id)
        else:
            if operation != "status" and "job_id" not in call.data:
                raise validation_error("sync_job")
            job = await manager.async_job(sn, call.data.get("job_id"))
            if operation == "continue":
                job = await manager.async_advance(sn, job.job_id)
            elif operation == "cancel":
                job = await manager.async_cancel(sn, job.job_id)
    except ValueError as err:
        raise validation_error("history_range") from err
    except ArchiveError as err:
        co.statistics.record_error(err)
        raise validation_error("statistics_unavailable") from err
    assert_query_scope(hass, call.data["device_id"], entry.entry_id, sn, False, require_live=False)
    return {"schema_version": 1, "operation": operation, **public_progress(job, dt_util.utcnow())}
