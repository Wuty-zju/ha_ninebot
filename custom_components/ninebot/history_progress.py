"""Scoped checkpoint evidence and public scalar progress; no I/O, raw data or GPS."""

from dataclasses import dataclass
from datetime import datetime

from .detail_health import HistoryHealth
from .history_plan import HistoryPlan, HistoryState, month_count

TERMINAL = {HistoryState.COMPLETE, HistoryState.WITH_GAPS, HistoryState.RECONCILED}


@dataclass(frozen=True)
class HistoryEvidence:
    baseline: HistoryPlan | None = None
    health: HistoryHealth | None = None
    active: tuple[HistoryPlan, ...] = ()
    last_success: datetime | None = None
    budget_at: datetime | None = None
    paused: bool = False
    requested: bool = False
    last_completed: datetime | None = None
    multi_page_months: int = 0


@dataclass(frozen=True)
class HistoryProgress:
    state: str
    scanned_months: int | None
    verified_months: int | None
    total_months: int | None
    gap_months: int | None
    discovered: int | None
    saved: int | None
    missing: int | None
    unmapped: int | None
    quality_pending: int | None
    simplified: int | None
    discovery_complete: bool
    active_months: int
    last_success: datetime | None
    last_completed: datetime | None = None
    next_request_at: datetime | None = None
    retry_at: datetime | None = None
    phase: str | None = None
    multi_page_months: int = 0
    reference_issues: int = 0

    @property
    def scan_percent(self) -> int | None:
        if self.total_months is None or self.verified_months is None:
            return None
        return min(100, self.verified_months * 100 // self.total_months)

    @property
    def detail_percent(self) -> int | None:
        if self.discovered is None or self.saved is None:
            return None
        if self.discovered == 0:
            return 100 if self.discovery_complete else None
        return min(100, self.saved * 100 // self.discovered)


def project_progress(
    evidence: HistoryEvidence,
    now: datetime,
    *,
    enabled: bool,
    paused: bool,
    authenticated: bool,
    authentication_required: bool = False,
    storage_error: str | None = None,
    active_phase: str | None = None,
) -> HistoryProgress:
    plan, health = evidence.baseline, evidence.health
    pending = plan is not None and plan.state not in TERMINAL
    current = plan if pending else next(iter(evidence.active), plan)
    phase = (
        active_phase
        or ("discovering" if current and current.next_month is not None else None)
        or ("preserving" if pending else "incremental" if evidence.active else None)
    )
    retry = (
        datetime.fromisoformat(current.retry_at)
        if current and current.state is HistoryState.WAITING and current.retry_at
        else None
    )
    deadlines = [value for value in (evidence.budget_at, retry) if value and value > now]
    next_request = max(deadlines) if deadlines else None
    if paused:
        state = "paused"
    elif not enabled:
        state = "disabled"
    elif storage_error:
        state = "storage_full" if storage_error == "capacity" else "storage_error"
    elif authentication_required:
        state = "needs_login"
    elif not authenticated:
        state = "waiting_connection"
    elif health and health.reference_issues:
        state = "verifying"  # A historical completion timestamp is not current consistency proof.
    elif active_phase is not None:
        state = active_phase
    elif current is None:
        state = "not_started"
    elif current.state is HistoryState.WAITING:
        state = "needs_login" if current.reason == "auth" else "waiting_retry"
    elif (
        (pending or evidence.active)
        and current.state is not HistoryState.QUERYING
        and evidence.budget_at
        and evidence.budget_at > now
    ):
        state = "waiting_budget"
    elif pending:
        state = {
            HistoryState.DISCOVER: "discovering",
            HistoryState.QUERYING: "discovering",
            HistoryState.PRESERVE: "preserving",
            HistoryState.VERIFY: "verifying",
            HistoryState.PAUSED: "paused",
        }.get(current.state, "waiting_retry")
    elif evidence.active:
        state = "incremental"
    else:
        state = "with_gaps" if current.state is HistoryState.WITH_GAPS else "synced"
    return HistoryProgress(
        state=state,
        scanned_months=plan.scanned_months if plan else None,
        verified_months=plan.verified_months if plan else None,
        total_months=month_count(plan.start_month, plan.end_month) if plan else None,
        gap_months=plan.gap_months if plan else None,
        discovered=health.discovered if health else None,
        saved=health.saved if health else None,
        missing=health.missing if health else None,
        unmapped=health.unmapped if health else None,
        quality_pending=health.quality_pending if health else None,
        simplified=health.simplified if health else None,
        discovery_complete=plan.discovery_complete if plan else False,
        active_months=len(evidence.active),
        last_success=evidence.last_success,
        last_completed=evidence.last_completed,
        next_request_at=next_request
        if enabled and not paused and authenticated and not storage_error
        else None,
        retry_at=retry,
        phase=phase,
        multi_page_months=evidence.multi_page_months,
        reference_issues=health.reference_issues if health else 0,
    )
