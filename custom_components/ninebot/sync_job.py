"""A single bounded account-local history job; no HA state or raw data."""

import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum

from .archive_codec import stamp
from .parsing import previous_month


class SyncState(StrEnum):
    READY = "ready"
    QUERYING = "querying"
    WAITING = "waiting"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


REASONS = frozenset(
    {"connection", "service", "protocol", "auth", "busy", "unavailable", "storage", "cancelled"}
)


def month_count(start: str, end: str) -> int:
    previous_month(start)
    previous_month(end)
    count = (int(end[:4]) - int(start[:4])) * 12 + int(end[4:]) - int(start[4:]) + 1
    if not 1 <= count <= 360:
        raise ValueError("Invalid sync range")
    return count


@dataclass(frozen=True)
class SyncJob:
    job_id: str
    vehicle: str
    start_month: str
    end_month: str
    next_month: str | None
    created_at: str
    updated_at: str
    state: SyncState = SyncState.READY
    revision: int = 1
    processed: int = 0
    queried: int = 0
    skipped: int = 0
    incomplete_months: tuple[str, ...] = ()
    unknown_months: tuple[str, ...] = ()
    failures: int = 0
    retry_at: str | None = None
    reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {SyncState.COMPLETE, SyncState.CANCELLED}

    def advance(self, now: str, *, queried: bool, complete: bool | None) -> "SyncJob":
        """One accepted month, regardless of unresolved upstream pagination."""
        if self.next_month is None or self.terminal:
            raise ValueError("No sync month")
        month = self.next_month
        following = None if month == self.start_month else previous_month(month)
        return replace(
            self,
            next_month=following,
            state=SyncState.COMPLETE if following is None else SyncState.READY,
            revision=self.revision + 1,
            processed=self.processed + 1,
            queried=self.queried + int(queried),
            skipped=self.skipped + int(not queried),
            incomplete_months=(*self.incomplete_months, month)
            if complete is False
            else self.incomplete_months,
            unknown_months=(*self.unknown_months, month)
            if complete is None
            else self.unknown_months,
            updated_at=now,
            failures=0,
            retry_at=None,
            reason=None,
        )


def job_data(job: SyncJob) -> dict[str, object]:
    return asdict(job)


def restored_job(data: object) -> SyncJob:
    if not isinstance(data, dict) or set(data) != set(SyncJob.__dataclass_fields__):
        raise ValueError("Invalid sync job shape")
    for key, width in (("job_id", 32), ("vehicle", 64)):
        if not isinstance(data[key], str) or not re.fullmatch(f"[0-9a-f]{{{width}}}", data[key]):
            raise ValueError("Invalid sync identity")
    count = month_count(data["start_month"], data["end_month"])
    stamp(data["created_at"])
    stamp(data["updated_at"])
    if stamp(data["updated_at"]) < stamp(data["created_at"]):
        raise ValueError("Invalid sync time")
    state = SyncState(data["state"])
    for key, lower, upper in (
        ("revision", 1, 2**63 - 1),
        ("processed", 0, count),
        ("queried", 0, count),
        ("skipped", 0, count),
        ("failures", 0, 255),
    ):
        if type(data[key]) is not int or not lower <= data[key] <= upper:
            raise ValueError("Invalid sync count")
    if data["queried"] + data["skipped"] != data["processed"]:
        raise ValueError("Inconsistent sync count")
    remaining = data["next_month"]
    if remaining is not None:
        if month_count(data["start_month"], remaining) != count - data["processed"]:
            raise ValueError("Invalid sync checkpoint")
    elif data["processed"] != count or state is not SyncState.COMPLETE:
        raise ValueError("Invalid completed sync job")
    if (state is SyncState.COMPLETE) != (remaining is None):
        raise ValueError("Invalid sync state")
    reason = data["reason"]
    if reason is not None and reason not in REASONS:
        raise ValueError("Invalid sync reason")
    if state in {SyncState.READY, SyncState.QUERYING, SyncState.COMPLETE} and reason is not None:
        raise ValueError("Unexpected sync reason")
    if state is SyncState.CANCELLED and reason != "cancelled":
        raise ValueError("Invalid cancellation reason")
    if state is SyncState.WAITING and (reason is None or reason == "cancelled"):
        raise ValueError("Invalid waiting reason")
    if data["retry_at"] is not None:
        if state is not SyncState.WAITING or stamp(data["retry_at"]) < stamp(data["updated_at"]):
            raise ValueError("Invalid sync retry")
    lists = {}
    for key in ("incomplete_months", "unknown_months"):
        rows = data[key]
        if (
            not isinstance(rows, list)
            or len(rows) > data["processed"]
            or len(set(rows)) != len(rows)
        ):
            raise ValueError("Invalid sync coverage")
        for month in rows:
            if not isinstance(month, str) or not data["start_month"] <= month <= data["end_month"]:
                raise ValueError("Invalid sync coverage month")
            previous_month(month)
            if remaining is not None and month <= remaining:
                raise ValueError("Unprocessed sync coverage month")
        lists[key] = tuple(rows)
    if set(lists["incomplete_months"]) & set(lists["unknown_months"]):
        raise ValueError("Overlapping sync coverage")
    return SyncJob(**{**data, "state": state, **lists})


def public_progress(job: SyncJob, now: datetime) -> dict[str, object]:
    """No hashed/raw vehicle identity, endpoint payload or token in responses."""
    retry = max(0, int((stamp(job.retry_at) - now).total_seconds())) if job.retry_at else None
    return {
        "job_id": job.job_id,
        "state": job.state.value,
        "revision": job.revision,
        "start_month": job.start_month,
        "end_month": job.end_month,
        "next_month": job.next_month,
        "processed_months": job.processed,
        "total_months": month_count(job.start_month, job.end_month),
        "queried_months": job.queried,
        "locally_skipped_months": job.skipped,
        "incomplete_months": list(job.incomplete_months),
        "unknown_months": list(job.unknown_months),
        "reason": job.reason,
        "retry_after_s": retry,
        "resume_required": not job.terminal,
        "all_rides_complete": job.state is SyncState.COMPLETE
        and not job.incomplete_months
        and not job.unknown_months,
        "storage": "ride_archive",
        "upstream_pagination_verified": False,
    }
