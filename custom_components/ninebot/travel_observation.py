"""Normalized month query result; disk facts are never represented as raw JSON."""

from dataclasses import dataclass
from datetime import datetime

from .models import TravelMonth


@dataclass(frozen=True)
class MonthObservation:
    travel: TravelMonth
    received_at: datetime
    backend_version: str | None
    source_mode: str
    current_sample_stale: bool = False
