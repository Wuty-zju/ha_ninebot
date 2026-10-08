"""Pure, bounded conversion of the validated v1 statistics ledger.

The original Store is never changed. Missing detail mappings, maximum speeds
and field precision cannot be reconstructed from its reduced scalar records.
"""

import hashlib
from calendar import monthrange
from dataclasses import asdict
from datetime import date

from .archive_codec import decoded, encoded, month_data, restored_month, restored_ride, ride_data
from .models import TravelMonth
from .month_summary import DailyMileage, MonthSummary
from .raw import Endpoint
from .ride_models import FieldState, Ride
from .statistics_store import MAX_MONTHS, MAX_RIDES, StoredMonth, StoredRide, timestamp


def legacy_ride(row: StoredRide) -> Ride:
    names = ("started_at", "ended_at", "distance_m", "duration_s", "energy_wh")
    states = tuple(
        (
            "energy_raw" if key == "energy_wh" else key,
            FieldState.VALID if key in row.current_fields else FieldState.MISSING,
        )
        for key in names
    )
    ride = Ride(
        row.source_month,
        Endpoint.TRAVEL,
        row.ride_id,
        started_at=timestamp(row.started_at) if row.started_at else None,
        ended_at=timestamp(row.ended_at) if row.ended_at else None,
        distance_m=row.distance_m,
        duration_s=row.duration_s,
        energy_raw=row.energy_wh,
        parser_contract="ninebot-statistics-v1-import",
        field_states=states,
        field_sources=tuple((key, "legacy_statistics_v1") for key, _ in states),
        issues=("legacy_detail_mapping_unavailable", "legacy_precision_unavailable"),
    )
    return restored_ride(decoded(encoded(ride_data(ride))))


def legacy_month(row: StoredMonth) -> TravelMonth:
    days = monthrange(int(row.month[:4]), int(row.month[4:]))[1]
    warnings = ["legacy_reduced_ride_metadata"]
    chart = row.chart_status
    # v1 retained the chart values only when consistent. Keep that loss explicit,
    # rather than inventing the missing inconsistent/unverified series.
    if chart in {"inconsistent", "total_unavailable"}:
        warnings.append(f"legacy_chart_{chart}_not_preserved")
        chart = "invalid"
    if row.daily_distance_km and (chart != "valid" or len(row.daily_distance_km) != days):
        raise ValueError("Invalid legacy chart")
    ids = row.returned_ids
    count = len(ids)
    complete = row.list_complete
    if complete is True and row.ride_count != count:
        complete = False
        warnings.append("legacy_membership_truncated")
    summary = MonthSummary(
        row.month,
        row.distance_km,
        row.energy_wh,
        row.ride_count,
        row.duration_s,
        tuple(
            DailyMileage(date(int(row.month[:4]), int(row.month[4:]), index), value)
            for index, value in enumerate(row.daily_distance_km, 1)
        ),
        chart,
        count,
        count,
        complete,
        count / row.ride_count
        if row.ride_count and count <= row.ride_count
        else (1.0 if row.ride_count == count == 0 else None),
        None,
        None,
        None,
        tuple(warnings),
    )
    return restored_month(decoded(encoded(month_data(TravelMonth(row.month, summary=summary)))))


def prepare_legacy(
    months: dict[str, dict[str, StoredMonth]],
    rides: dict[str, dict[str, StoredRide]],
) -> tuple[
    str, tuple[tuple[str, StoredMonth, TravelMonth], ...], tuple[tuple[str, StoredRide, Ride], ...]
]:
    """Reject the whole import before any SQL writes; never create fake facts."""
    if sum(map(len, months.values())) > MAX_MONTHS or sum(map(len, rides.values())) > MAX_RIDES:
        raise ValueError("Invalid legacy budget")
    vehicles = set(months) | set(rides)
    if len(vehicles) > 128 or any(
        len(key) != 64 or any(c not in "0123456789abcdef" for c in key) for key in vehicles
    ):
        raise ValueError("Invalid legacy scope")
    digest = hashlib.sha256()
    converted_months, converted_rides = [], []
    for vehicle, records in sorted(months.items()):
        for key, row in sorted(records.items()):
            if not isinstance(row, StoredMonth) or row.month != key:
                raise ValueError("Invalid legacy month")
            timestamp(row.received_at)
            # The bounded row codec also checks identity lists and backend
            # metadata later in the archive's transaction preparation.
            digest.update(encoded({"vehicle": vehicle, "month": asdict(row)}).encode())
            converted_months.append((vehicle, row, legacy_month(row)))
    for vehicle, ride_records in sorted(rides.items()):
        for key, ride_row in sorted(ride_records.items()):
            if not isinstance(ride_row, StoredRide) or ride_row.ride_id != key:
                raise ValueError("Invalid legacy ride")
            timestamp(ride_row.received_at)
            digest.update(encoded({"vehicle": vehicle, "ride": asdict(ride_row)}).encode())
            converted_rides.append((vehicle, ride_row, legacy_ride(ride_row)))
    return digest.hexdigest(), tuple(converted_months), tuple(converted_rides)
