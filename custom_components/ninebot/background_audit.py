"""One low-priority local batch per preservation tick, with no network budget."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .archive_audit import AuditPlan
from .ride_archive import ArchiveError, ArchiveFailure

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator


@dataclass(frozen=True)
class AuditView:
    phase: str
    checked: int
    total: int
    invalid: int
    observed_at: str
    scope: str = "captured_scan"


class BackgroundAudit:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.views: dict[str, AuditView] = {}

    def publish(self, sn: str, plan: AuditPlan | None, owns: Callable[[], bool]) -> None:
        if not owns():
            self.views.pop(sn, None)
            return
        if plan is None:
            return
        # Counts describe the captured local scan, not a current/cloud certificate.
        previous = self.views.get(sn)
        if (
            previous is not None
            and previous.scope == "current_object_receipts"
            and previous.observed_at > plan.updated_at
            and plan.complete
        ):
            return
        value = AuditView(plan.phase, plan.checked, plan.total, plan.invalid, plan.updated_at)
        if self.views.get(sn) != value:
            self.views[sn] = value
            self.co.statistics._notify_change(sn, owns)

    async def async_step(self, now: datetime) -> None:
        co, archive = self.co, self.co.statistics.archive
        entry = co.config_entry
        if (
            co._stopping
            or entry is None
            or entry.options.get("preserve_history") is not True
            or entry.options.get("auto_history_sync", True) is not True
            or not co.statistics.writable
            or not archive.background_idle
        ):
            return
        vehicles = tuple(sn for sn in co.data if co.local_vehicle_available(sn))
        for sn in tuple(self.views):
            if sn not in vehicles:
                self.views.pop(sn, None)
        vehicles = await archive.async_audit_order(vehicles)
        for sn in vehicles:
            owns = co.preservation.bootstrap.guard(sn, require_enabled=False)
            if not owns():
                continue
            result = await archive.async_background_history_audit(sn, now, guard=owns)
            if result is not None:
                if isinstance(result, AuditPlan):
                    self.publish(sn, result, owns)
                elif owns():
                    value = AuditView(
                        "delta",
                        result.total - result.pending,
                        result.total,
                        result.invalid,
                        now.isoformat(),
                        "current_object_receipts",
                    )
                    if self.views.get(sn) != value:
                        self.views[sn] = value
                        co.statistics._notify_change(sn, owns)
                if result.invalid:
                    co.statistics.record_error(ArchiveError(ArchiveFailure.OBJECT))
                return  # One vehicle/batch, then yield to live work and other actors.
