"""Durable full-history contracts, independent of the legacy selected-range job.

These are checkpoint facts, never proof of cloud completeness by themselves.
No credentials, precise locations or upstream payloads belong in root metadata.
"""

import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any

from .archive_codec import stamp
from .parsing import previous_month

CONTRACT_VERSION = 1
MAX_MONTHS = 1200
MAX_PAGES = 4096
PROVIDERS = frozenset({"native-history-v1"})
REASONS = frozenset(
    {
        "auth",
        "connection",
        "service",
        "protocol",
        "storage",
        "capacity",
        "budget",
        "paused",
        "range_unknown",
        "repeated_page",
        "unstable_total",
        "missing_total",
        "empty_page",
        "page_limit",
        "schema",
        "unavailable",
    }
)


class HistoryState(StrEnum):
    DISCOVER = "discover"
    QUERYING = "querying"
    PRESERVE = "preserve"
    VERIFY = "verify"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETE = "complete"
    WITH_GAPS = "completed_with_gaps"


def month_count(start: str, end: str) -> int:
    """Full bootstrap is not capped by the legacy 360-month action limit."""
    previous_month(start)
    previous_month(end)
    count = (int(end[:4]) - int(start[:4])) * 12 + int(end[4:]) - int(start[4:]) + 1
    if not "200001" <= start <= end <= "209912" or not 1 <= count <= MAX_MONTHS:
        raise ValueError("Invalid history range")
    return count


@dataclass(frozen=True)
class HistoryPlan:
    plan_id: str
    vehicle: str
    start_month: str
    end_month: str
    cutoff: str
    created_at: str
    updated_at: str
    provider: str = "native-history-v1"
    contract: int = CONTRACT_VERSION
    revision: int = 1
    state: HistoryState = HistoryState.DISCOVER
    next_month: str | None = None
    next_page: int = 1
    scanned_months: int = 0
    verified_months: int = 0
    gap_months: int = 0
    discovery_complete: bool = False
    retry_at: str | None = None
    reason: str | None = None
    attempts: int = 0

    @classmethod
    def create(cls, plan_id: str, vehicle: str, start: str, end: str, now: str) -> "HistoryPlan":
        plan = cls(plan_id, vehicle, start, end, now, now, now, next_month=end)
        return restored(asdict(plan))

    def page_committed(self, now: str, *, finished: bool, verified: bool) -> "HistoryPlan":
        """Called only in the transaction that persists the accepted page evidence."""
        if (
            self.state not in {HistoryState.DISCOVER, HistoryState.QUERYING}
            or self.next_month is None
        ):
            raise ValueError("No active discovery checkpoint")
        if not finished:
            if verified or self.next_page >= MAX_PAGES:
                raise ValueError("Invalid continuing page")
            return restored(
                asdict(
                    replace(
                        self,
                        next_page=self.next_page + 1,
                        revision=self.revision + 1,
                        updated_at=now,
                        state=HistoryState.DISCOVER,
                        retry_at=None,
                        attempts=0,
                    )
                )
            )
        following = None if self.next_month == self.start_month else previous_month(self.next_month)
        return restored(
            asdict(
                replace(
                    self,
                    next_month=following,
                    next_page=1,
                    state=HistoryState.PRESERVE if following is None else HistoryState.DISCOVER,
                    discovery_complete=following is None,
                    scanned_months=self.scanned_months + 1,
                    verified_months=self.verified_months + int(verified),
                    gap_months=self.gap_months + int(not verified),
                    updated_at=now,
                    revision=self.revision + 1,
                    retry_at=None,
                    attempts=0,
                )
            )
        )


def restored(data: object) -> HistoryPlan:
    """Strict persisted schema; old SyncJob.COMPLETE cannot decode as a baseline."""
    if not isinstance(data, dict) or set(data) != set(HistoryPlan.__dataclass_fields__):
        raise ValueError("Invalid history root schema")
    for key, width in (("plan_id", 32), ("vehicle", 64)):
        if not isinstance(data[key], str) or not re.fullmatch(f"[0-9a-f]{{{width}}}", data[key]):
            raise ValueError("Invalid history root scope")
    total = month_count(data["start_month"], data["end_month"])
    if (
        data["provider"] not in PROVIDERS
        or type(data["contract"]) is not int
        or data["contract"] != 1
    ):
        raise ValueError("Unsupported history contract")
    created, updated, cutoff = (stamp(data[k]) for k in ("created_at", "updated_at", "cutoff"))
    if cutoff != created or updated < created:
        raise ValueError("Invalid history root timestamp")
    state = HistoryState(data["state"])
    for key, minimum, maximum in (
        ("revision", 1, 2**63 - 1),
        ("next_page", 1, MAX_PAGES),
        ("scanned_months", 0, total),
        ("verified_months", 0, total),
        ("gap_months", 0, total),
        ("attempts", 0, 255),
    ):
        if type(data[key]) is not int or not minimum <= data[key] <= maximum:
            raise ValueError("Invalid history root count")
    if data["verified_months"] + data["gap_months"] != data["scanned_months"]:
        raise ValueError("Inconsistent history root coverage")
    complete = data["discovery_complete"]
    if type(complete) is not bool:
        raise ValueError("Invalid history discovery state")
    remaining = data["next_month"]
    if remaining is None:
        if not complete or data["scanned_months"] != total or data["next_page"] != 1:
            raise ValueError("Invalid finished discovery")
    elif complete or month_count(data["start_month"], remaining) != total - data["scanned_months"]:
        raise ValueError("Invalid history cursor")
    if state in {HistoryState.DISCOVER, HistoryState.QUERYING} and complete:
        raise ValueError("Invalid discovery phase")
    if (
        state
        in {
            HistoryState.PRESERVE,
            HistoryState.VERIFY,
            HistoryState.COMPLETE,
            HistoryState.WITH_GAPS,
        }
        and not complete
    ):
        raise ValueError("Discovery is not complete")
    if state is HistoryState.COMPLETE and data["gap_months"]:
        raise ValueError("Cannot hide history gaps")
    reason = data["reason"]
    if reason is not None and reason not in REASONS:
        raise ValueError("Invalid history reason")
    if (state in {HistoryState.WAITING, HistoryState.PAUSED}) != (reason is not None):
        raise ValueError("Inconsistent history waiting state")
    if data["retry_at"] is not None:
        if (
            state not in {HistoryState.WAITING, HistoryState.QUERYING}
            or stamp(data["retry_at"]) < updated
        ):
            raise ValueError("Invalid history retry")
    elif state is HistoryState.QUERYING:
        raise ValueError("Missing history claim lease")
    return HistoryPlan(**{**data, "state": state})


def plan_data(plan: HistoryPlan) -> dict[str, Any]:
    result = asdict(plan)
    restored(result)
    return result
