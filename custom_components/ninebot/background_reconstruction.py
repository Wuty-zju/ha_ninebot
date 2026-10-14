"""Resume explicitly requested local recovery on the existing low-priority tick."""

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .reconstruction_job import ReconstructionJob
from .ride_archive import ArchiveError, ArchiveFailure

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator


def public(job: ReconstructionJob | None) -> dict:
    return {
        "contract": "local_scalar_quality_reconstruction_v2"
        if job and job.contract == 2
        else "local_scalar_reconstruction_v1",
        "checker_contract": job.contract if job else None,
        "scope": "captured_local_details",
        "state": job.state if job else "not_started",
        "job_id": job.job_id if job else None,
        "total": job.total if job else 0,
        "checked": job.checked if job else 0,
        "repaired": job.repaired if job else 0,
        "blocked": job.blocked if job else 0,
        "corrupt": job.corrupt if job else 0,
        "recovery_point_ready": bool(job and job.backup_hash and job.state != "blocked_recovery"),
        "archive_changed": job.changed if job else False,
        "updated_at": job.updated_at if job else None,
        "references_verified": False,
        "all_normalized_fields_verified": False,
        "cloud_queries": 0,
    }


class BackgroundReconstruction:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.views: dict[str, ReconstructionJob] = {}

    async def publish(self, sn: str, job: ReconstructionJob | None) -> None:
        if job is None:
            return
        owns = self.co.preservation.bootstrap.guard(sn, require_enabled=False, respect_pause=False)
        if not owns():
            return
        before = self.views.get(sn)
        if (job.corrupt and (before is None or job.corrupt != before.corrupt)) or (
            job.state == "blocked_recovery" and (before is None or before.state != job.state)
        ):
            self.co.statistics.record_error(ArchiveError(ArchiveFailure.OBJECT))
        if before != job:
            if job.repaired > (before.repaired if before else 0):
                await self.co.statistics.async_reconstruction_committed(sn, owns)
                await self.co.preservation.async_refresh_progress((sn,), dt_util.utcnow())
            if owns():
                self.views[sn] = job
                self.co.statistics._notify_change(sn, owns)

    async def async_step(self, now: datetime) -> None:
        co, archive = self.co, self.co.statistics.archive
        entry = co.config_entry
        if (
            co._stopping
            or entry is None
            or entry.options.get("preserve_history") is not True
            or not co.statistics.writable
            or not archive.background_idle
        ):
            return
        vehicles = tuple(sorted(sn for sn in co.data if co.local_vehicle_available(sn)))
        for sn in tuple(self.views):
            if sn not in vehicles:
                self.views.pop(sn, None)
        vehicles = await archive.async_reconstruction_order(tuple(vehicles))
        for sn in vehicles:
            owns = co.preservation.bootstrap.guard(sn, require_enabled=False)
            if not owns():
                continue
            job = await archive.async_reconstruction_job(sn, "status", now, guard=owns)
            await self.publish(sn, job)
            if job is None or job.terminal or job.state in {"paused", "blocked_recovery"}:
                continue
            result = await archive.async_reconstruction_job(sn, "step", now, guard=owns)
            await self.publish(sn, result)
            return  # At most one ride; no cloud budget or independent timer.
