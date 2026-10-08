"""Immutable statistical views over selected archive months, without eviction.

The month membership remains distinct from the global ride identity index. A
ride seen in two query months is not lost when its latest source month changes.
"""

from dataclasses import dataclass, field
from typing import Protocol

from .ride_archive import ArchiveMonth, vehicle_key
from .ride_models import FieldState, Ride
from .statistics_store import StoredMonth, StoredRide


class StatisticsReader(Protocol):
    @property
    def source_mode(self) -> str: ...

    def month(self, sn: str, month: str) -> StoredMonth | None: ...

    def month_rides(self, sn: str, month: str) -> tuple[tuple[StoredRide, ...], bool]: ...


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


@dataclass(frozen=True)
class ArchiveStatisticsView:
    """One atomic bounded SQL read; no HA objects or mutable store dictionaries."""

    source_mode: str = "ride_archive"
    records: dict[tuple[str, str], StoredMonth] = field(default_factory=dict)
    selections: dict[tuple[str, str], tuple[tuple[StoredRide, ...], bool]] = field(
        default_factory=dict
    )

    def month(self, sn: str, month: str) -> StoredMonth | None:
        return self.records.get((vehicle_key(sn), month))

    def month_rides(self, sn: str, month: str) -> tuple[tuple[StoredRide, ...], bool]:
        return self.selections.get((vehicle_key(sn), month), ((), False))

    @classmethod
    def from_months(cls, sn: str, months: tuple[ArchiveMonth, ...]) -> "ArchiveStatisticsView":
        records, selections = {}, {}
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
            selections[key] = (
                tuple(statistical_ride(ride, archived) for ride in travel.rides),
                archived.complete,
            )
        return cls(records=records, selections=selections)
