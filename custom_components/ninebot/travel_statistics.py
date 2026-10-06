"""Explicit, scope-matched Wh/km; never infer full history from returned rows."""

import hashlib
import json
import math
from typing import Any

from .month_summary import sum_rides
from .parsing import number
from .ride_models import Ride


def energy_statistics(
    distance_km: float | None, energy_wh: float | None, *, basis: str
) -> dict[str, Any]:
    distance_km = number(distance_km, 0)
    energy_wh = number(energy_wh, 0)
    intensity = None
    if (
        distance_km is not None
        and energy_wh is not None
        and math.isfinite(distance_km)
        and math.isfinite(energy_wh)
        and distance_km > 0
        and energy_wh >= 0
    ):
        value = energy_wh / distance_km
        if math.isfinite(value):
            intensity = value
    return {
        "basis": basis,
        "distance_km": distance_km,
        "energy_wh": energy_wh,
        "energy_intensity_wh_per_km": intensity,
        "energy_unit_evidence": "maintainer_confirmed",
        "method": "sum_energy_wh_divided_by_sum_distance_km",
    }


def ride_signature(ride: Ride) -> str:
    """Compare only stable normalized metrics, not raw aliases or query month."""
    return hashlib.sha256(
        json.dumps(
            [
                ride.started_at.isoformat() if ride.started_at else None,
                ride.ended_at.isoformat() if ride.ended_at else None,
                ride.distance_m,
                ride.duration_s,
                ride.energy_raw,
                ride.server_max_speed_m_s,
            ]
        ).encode()
    ).hexdigest()


def returned_statistics(rides: tuple[Ride, ...]) -> dict[str, Any]:
    unique: dict[str, Ride] = {}
    complete = True
    for ride in rides:
        if ride.ride_id is None:
            complete = False
        elif ride.ride_id in unique:
            if ride_signature(unique[ride.ride_id]) != ride_signature(ride):
                complete = False
        else:
            unique[ride.ride_id] = ride
    selected = tuple(unique.values())
    distance = sum_rides(selected, "distance_m") if complete else None
    energy = sum_rides(selected, "energy_raw") if complete else None
    return {
        **energy_statistics(
            distance / 1000 if distance is not None else None,
            energy,
            basis="returned_unique_rides",
        ),
        "observed_row_count": len(rides),
        "unique_ride_count": len(unique),
        "identity_complete": complete,
        "metrics_complete": distance is not None and energy is not None,
        "upstream_completeness": "see_month_coverage",
    }
