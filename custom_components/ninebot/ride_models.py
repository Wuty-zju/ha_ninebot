"""Immutable travel domain data; raw payload ownership stays in RawStore."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .raw import Endpoint, RawReference


class FieldState(StrEnum):
    """Protocol presence, separate from normalized value and freshness."""

    MISSING = "missing"
    NULL = "null"
    INVALID = "invalid"
    VALID = "valid"
    EMPTY = "empty"


@dataclass(frozen=True)
class SpeedSample:
    sequence: int
    raw: float
    speed_m_s: float | None = None
    timestamp: datetime | None = None
    source_path: str = "trail"


@dataclass(frozen=True)
class RideTrackPoint:
    latitude: float
    longitude: float
    sequence: int
    speed_raw: float | None = None
    distance_delta_raw: float | None = None
    timestamp: datetime | None = None
    speed_m_s: float | None = None
    distance_delta_m: float | None = None
    heading_deg: float | None = None
    altitude_m: float | None = None
    coordinate_system: str = "unknown"


@dataclass(frozen=True)
class Ride:
    query_month: str
    source: Endpoint
    ride_id: str | None = None
    detail_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    distance_m: float | None = None
    duration_s: float | None = None
    energy_raw: float | None = None
    used_electricity_raw: float | None = None
    speed_raw: float | None = None
    server_max_speed_m_s: float | None = None
    server_average_speed_raw: float | None = None
    speed_samples: tuple[SpeedSample, ...] = ()
    track_points: tuple[RideTrackPoint, ...] = ()
    total_track_points: int | None = None
    track_truncated: bool = False
    issues: tuple[str, ...] = ()
    field_provenance: tuple[tuple[str, str], ...] = ()
    parser_contract: str = "ninecli-0.1.7-travel-v1"
    field_states: tuple[tuple[str, FieldState], ...] = ()
    field_sources: tuple[tuple[str, str], ...] = ()
    detail_field_states: tuple[tuple[str, FieldState], ...] = ()

    @property
    def average_speed_m_s(self) -> float | None:
        """Overall distance/duration, never replaced by a sample arithmetic mean."""
        if self.distance_m is None or self.duration_s is None or self.duration_s <= 0:
            return None
        return self.distance_m / self.duration_s

    @property
    def sample_mean_speed_m_s(self) -> float | None:
        values = [sample.speed_m_s for sample in self.speed_samples]
        if not values or any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None) / len(values)

    @property
    def sample_max_speed_m_s(self) -> float | None:
        values = [sample.speed_m_s for sample in self.speed_samples]
        if not values or any(value is None for value in values):
            return None
        return max(value for value in values if value is not None)

    @property
    def start_location(self) -> RideTrackPoint | None:
        return self.track_points[0] if self.track_points else None

    @property
    def end_location(self) -> RideTrackPoint | None:
        return self.track_points[-1] if self.track_points else None


@dataclass(frozen=True)
class RideDetail:
    """One scoped detail result, with no second copy of the raw payload."""

    requested_detail_id: str
    query_month: str
    received_at: datetime
    ride: Ride
    backend_version: str | None = None
    endpoint_version: str | None = None
    raw_reference: RawReference | None = None
