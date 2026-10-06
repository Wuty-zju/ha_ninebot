"""Bounded per-entry history continuations, never a background import."""

import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property

from .ride_models import Ride

MAX_HISTORY_IDS = 20000
HISTORY_TTL = 900
HISTORY_BUDGET = 20 * 1024 * 1024


@dataclass(frozen=True)
class HistoryState:
    entry_id: str
    vehicle: str
    start_month: str
    end_month: str
    next_month: str | None
    scanned_months: tuple[str, ...] = ()
    incomplete_months: tuple[str, ...] = ()
    unknown_months: tuple[str, ...] = ()
    totals: tuple[float | None, ...] = (0, 0, 0, 0)
    seen: dict[str, str] = field(default_factory=dict)
    pending: tuple[Ride, ...] = ()
    warnings: tuple[str, ...] = ()
    stopped_reason: str | None = None
    indexed_totals: tuple[float | None, float | None] = (0, 0)
    indexed_identity_complete: bool = True

    @property
    def scan_complete(self) -> bool:
        return self.next_month is None and self.stopped_reason is None

    @property
    def rides_complete(self) -> bool | None:
        if (
            not self.scan_complete
            or self.incomplete_months
            or "conflicting_ride_id" in self.warnings
        ):
            return False
        return None if self.unknown_months else True

    @cached_property
    def retained_cost(self) -> int:
        # Conservative charges for immutable Ride models (no tracks/samples in
        # a month response), identity signatures, dictionaries and small ledgers.
        return (
            4096
            + sum(
                2048
                + 128
                * (
                    len(ride.field_states)
                    + len(ride.field_sources)
                    + len(ride.field_provenance)
                    + len(ride.detail_field_states)
                )
                for ride in self.pending
            )
            + sum(len(key.encode()) + len(value) + 256 for key, value in self.seen.items())
            + len(self.scanned_months) * 512
        )


@dataclass(frozen=True)
class HistorySummary:
    start_month: str
    end_month: str
    scanned_months: int
    indexed_rides: int
    mileage_km: float | None
    energy_wh: float | None
    duration_s: float | None
    range_complete: bool
    rides_complete: bool | None
    incomplete_months: int

    @classmethod
    def from_state(cls, state: HistoryState) -> "HistorySummary":
        return cls(
            state.start_month,
            state.end_month,
            len(state.scanned_months),
            len(state.seen),
            state.totals[0] if state.scanned_months else None,
            state.totals[1] if state.scanned_months else None,
            state.totals[3] if state.scanned_months else None,
            state.scan_complete,
            state.rides_complete,
            len(state.incomplete_months),
        )


class HistoryStore:
    def __init__(self) -> None:
        self._states: OrderedDict[str, tuple[datetime, HistoryState]] = OrderedDict()
        self.summary: dict[str, HistorySummary] = {}
        self.retry_at: dict[tuple[str, str], datetime] = {}

    def expire(self, now: datetime) -> None:
        for key, (stamp, _) in list(self._states.items()):
            if not 0 <= (now - stamp).total_seconds() < HISTORY_TTL:
                self._states.pop(key)
        self.retry_at = {key: stamp for key, stamp in self.retry_at.items() if stamp > now}

    def get(self, cursor: str, now: datetime) -> HistoryState | None:
        self.expire(now)
        value = self._states.get(cursor)
        if value is None:
            return None
        self._states.move_to_end(cursor)
        return value[1]

    def put(self, state: HistoryState, now: datetime) -> str | None:
        self.expire(now)
        if state.retained_cost > HISTORY_BUDGET:
            return None
        while self._states and (
            len(self._states) >= 8
            or sum(item.retained_cost for _, item in self._states.values()) + state.retained_cost
            > HISTORY_BUDGET
        ):
            self._states.popitem(last=False)
        cursor = secrets.token_hex(16)
        self._states[cursor] = now, state
        return cursor

    def clear(self) -> None:
        self._states.clear()
        self.summary.clear()
        self.retry_at.clear()
