"""Immutable statistical views over selected archive months, without eviction.

The month membership remains distinct from the global ride identity index. A
ride seen in two query months is not lost when its latest source month changes.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from .ride_archive import ArchiveMonth, vehicle_key
from .ride_models import FieldState, Ride
from .statistics_store import StoredMonth, StoredRide


class StatisticsReader(Protocol):
    @property
    def source_mode(self) -> str: ...

    def month(self, sn: str, month: str) -> StoredMonth | None: ...

    def month_rides(self, sn: str, month: str) -> tuple[tuple[StoredRide, ...], bool]: ...

    def month_metric_complete(self, sn: str, month: str, metric: str) -> bool: ...

    def month_completeness_basis(self, sn: str, month: str) -> str: ...


def statistical_ride(ride: Ride, month: ArchiveMonth) -> StoredRide:
    """Retained valid facts are usable; invalid time claims never become facts."""
    values = {
        "started_at": ride.started_at.isoformat() if ride.started_at else None,
        "ended_at": ride.ended_at.isoformat() if ride.ended_at else None,
        "distance_m": ride.distance_m,
        "duration_s": ride.duration_s,
        "energy_wh": ride.energy_raw,
    }
    conflicts = bool({"reversed_timestamps", "conflicting_time_representations"} & set(ride.issues))
    states = dict(ride.field_states)
    legacy = ride.parser_contract == "ninebot-statistics-v1-import"
    current = tuple(
        key
        for key, value in values.items()
        if value is not None
        and not (key in {"started_at", "ended_at"} and conflicts)
        and (
            not legacy
            or states.get("energy_raw" if key == "energy_wh" else key) is FieldState.VALID
        )
    )
    assert ride.ride_id is not None
    return StoredRide(
        ride.ride_id,
        month.travel.month,
        month.received_at.isoformat(),
        started_at=ride.started_at.isoformat() if ride.started_at else None,
        ended_at=ride.ended_at.isoformat() if ride.ended_at else None,
        distance_m=ride.distance_m,
        duration_s=ride.duration_s,
        energy_wh=ride.energy_raw,
        current_fields=current,
    )


def reconciled_metrics(
    archived: ArchiveMonth, rows: tuple[StoredRide, ...]
) -> tuple[bool, tuple[str, ...]]:
    """Count and distance anchor retained identities; metric conflicts stay separate.

    This is a consistency check against the latest monthly report, not an
    upstream claim that its single response contains every ride ID.
    """
    summary = archived.travel.summary
    assert summary is not None
    if (
        summary.ride_count is None
        or len(rows) != summary.ride_count
        or len({row.ride_id for row in rows}) != len(rows)
        or archived.missing_ids
        or any(
            ride.started_at is None
            or ride.ended_at is None
            or ride.started_at >= ride.ended_at
            or ride.ended_at > archived.received_at
            or {"reversed_timestamps", "conflicting_time_representations"} & set(ride.issues)
            for ride in archived.statistics_rides or ()
        )
    ):
        return False, ()
    precision = dict(archived.travel.precision)

    def matches(field: str, reported: float | None, raw_key: str, scale: int = 1) -> bool:
        if reported is None or any(
            getattr(row, field) is None or field not in row.current_fields for row in rows
        ):
            return False
        total = sum((Decimal(str(getattr(row, field))) / scale for row in rows), Decimal(0))
        digits = precision.get(raw_key)
        tolerance = Decimal("1e-9")
        if digits is not None:
            tolerance += Decimal(10) ** -digits / 2
        return abs(total - Decimal(str(reported))) <= tolerance

    if not matches("distance_m", summary.mileage_km, "total_mileages", 1000):
        return False, ()
    blocked = []
    if not matches("energy_wh", summary.energy_wh, "ec"):
        blocked.append("energy_wh")
    if not matches("duration_s", summary.duration_s, "duration") or any(
        ride.started_at is None
        or ride.ended_at is None
        or ride.duration_s is None
        or abs((ride.ended_at - ride.started_at).total_seconds() - ride.duration_s) > 1
        for ride in archived.statistics_rides or ()
    ):
        blocked.append("duration_s")
    return True, tuple(blocked)


@dataclass(frozen=True)
class ArchiveStatisticsView:
    """One atomic bounded SQL read; no HA objects or mutable store dictionaries."""

    source_mode: str = "ride_archive"
    records: dict[tuple[str, str], StoredMonth] = field(default_factory=dict)
    selections: dict[tuple[str, str], tuple[tuple[StoredRide, ...], bool]] = field(
        default_factory=dict
    )

    blocked_metrics: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    completeness_bases: dict[tuple[str, str], str] = field(default_factory=dict)

    def month(self, sn: str, month: str) -> StoredMonth | None:
        return self.records.get((vehicle_key(sn), month))

    def month_rides(self, sn: str, month: str) -> tuple[tuple[StoredRide, ...], bool]:
        return self.selections.get((vehicle_key(sn), month), ((), False))

    def month_metric_complete(self, sn: str, month: str, metric: str) -> bool:
        return metric not in self.blocked_metrics.get((vehicle_key(sn), month), ())

    def month_completeness_basis(self, sn: str, month: str) -> str:
        return self.completeness_bases.get((vehicle_key(sn), month), "incomplete")

    @classmethod
    def from_months(cls, sn: str, months: tuple[ArchiveMonth, ...]) -> "ArchiveStatisticsView":
        records, selections, blocked_metrics, bases = {}, {}, {}, {}
        for archived in months:
            travel = archived.travel
            summary = travel.summary
            assert summary is not None
            key = (vehicle_key(sn), travel.month)
            records[key] = StoredMonth(
                travel.month,
                archived.received_at.isoformat(),
                archived.revision,
                summary.mileage_km,
                summary.energy_wh,
                summary.ride_count,
                summary.duration_s,
                tuple(row.distance_km for row in summary.daily_mileage)
                if summary.chart_status == "valid"
                else (),
                summary.chart_status,
                summary.list_complete,
                archived.returned_ids,
                archived.backend_version,
            )
            candidates = (
                travel.rides if archived.statistics_rides is None else archived.statistics_rides
            )
            rows = tuple(statistical_ride(ride, archived) for ride in candidates)
            complete = archived.complete
            blocked: tuple[str, ...] = ()
            basis = "upstream_complete" if complete else "incomplete"
            if not complete and archived.statistics_rides is not None:
                complete, blocked = reconciled_metrics(archived, rows)
                if complete:
                    basis = "local_reconciled"
            selections[key] = rows, complete
            blocked_metrics[key] = blocked
            bases[key] = basis
        return cls(
            records=records,
            selections=selections,
            blocked_metrics=blocked_metrics,
            completeness_bases=bases,
        )
