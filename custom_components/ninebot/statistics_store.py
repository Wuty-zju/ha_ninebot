"""Bounded durable normalized month/ride statistics; never raw JSON or GPS."""

import hashlib
import json
from calendar import monthrange
from copy import copy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import TravelMonth
from .parsing import number, previous_month
from .raw import version_metadata
from .travel import opaque_id

MAX_MONTHS = 360
MAX_RIDES = 500
MAX_BYTES = 2 * 1024 * 1024
MAX_VEHICLES = 128


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Invalid statistics timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or not 2000 <= stamp.year <= 2099:
        raise ValueError("Invalid statistics timestamp")
    return stamp.astimezone(UTC)


def metric(value: object) -> float | None:
    if value is None:
        return None
    result = number(value, 0) if type(value) in {int, float} else None
    if result is None:
        raise ValueError("Invalid statistics metric")
    return result


@dataclass(frozen=True)
class StoredMonth:
    month: str
    received_at: str
    revision: int
    distance_km: float | None
    energy_wh: float | None
    ride_count: int | None
    duration_s: float | None
    daily_distance_km: tuple[float, ...]
    chart_status: str
    list_complete: bool | None
    returned_ids: tuple[str, ...]
    backend_version: str | None = None


@dataclass(frozen=True)
class StoredRide:
    ride_id: str
    source_month: str
    received_at: str
    started_at: str | None
    ended_at: str | None
    distance_m: float | None
    duration_s: float | None
    energy_wh: float | None
    current_fields: tuple[str, ...] = ()


def read_statistics(path: Path, key: str) -> dict[str, Any] | None:
    """Refuse future/corrupt files before HA Store can migrate/overwrite them."""
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(encoded) > MAX_BYTES:
        raise ValueError("Statistics budget exceeded")
    value = json.loads(encoded)
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("minor_version", 1) != 1
        or value.get("key") != key
        or not isinstance(value.get("data"), dict)
    ):
        raise ValueError("Unsupported statistics envelope")
    return value["data"]


class TravelStatisticsStore:
    """Account-local replace/upsert ledger with independent availability."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry_id}.travel_statistics_v1"
        )
        self._issue_id = f"travel_statistics_{entry_id}"
        self.months: dict[str, dict[str, StoredMonth]] = {}
        self.rides: dict[str, dict[str, StoredRide]] = {}
        self.available = True
        self.restored = False

    @staticmethod
    def vehicle_key(sn: str) -> str:
        return hashlib.sha256(sn.encode()).hexdigest()

    def month(self, sn: str, month: str) -> StoredMonth | None:
        return self.months.get(self.vehicle_key(sn), {}).get(month)

    def month_rides(self, sn: str, month: str) -> tuple[tuple[StoredRide, ...], bool]:
        record = self.month(sn, month)
        if record is None:
            return (), False
        rows = self.rides.get(self.vehicle_key(sn), {})
        selected = tuple(rows[key] for key in record.returned_ids if key in rows)
        complete = bool(
            record.list_complete is True
            and len(selected) == len(record.returned_ids)
            and len(selected) == record.ride_count
            and all(row.source_month == month for row in selected)
        )
        return selected, complete

    def dump(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "months": {
                vehicle: {
                    month: {
                        **asdict(row),
                        "daily_distance_km": list(row.daily_distance_km),
                        "returned_ids": list(row.returned_ids),
                    }
                    for month, row in months.items()
                }
                for vehicle, months in self.months.items()
            },
            "rides": {
                vehicle: {
                    key: {**asdict(row), "current_fields": list(row.current_fields)}
                    for key, row in rides.items()
                }
                for vehicle, rides in self.rides.items()
            },
        }

    def _problem(self) -> None:
        self.available = False
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="travel_statistics_storage",
        )

    async def async_load(self) -> None:
        try:
            raw = await self.hass.async_add_executor_job(
                read_statistics, Path(self._store.path), self._store.key
            )
            if raw is not None:
                await self.hass.async_add_executor_job(self.restore, raw)
                self.restored = True
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        except (OSError, ValueError, TypeError, KeyError):
            self._problem()

    def restore(self, raw: dict[str, Any]) -> None:
        """Validate the entire candidate before replacing runtime state."""
        if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
            raise ValueError("Unsupported statistics schema")
        months: dict[str, dict[str, StoredMonth]] = {}
        rides: dict[str, dict[str, StoredRide]] = {}
        for name, model in (("months", StoredMonth), ("rides", StoredRide)):
            containers = raw[name]
            if not isinstance(containers, dict) or len(containers) > MAX_VEHICLES:
                raise ValueError("Invalid statistics vehicles")
            for vehicle, values in containers.items():
                if (
                    not isinstance(vehicle, str)
                    or len(vehicle) != 64
                    or any(char not in "0123456789abcdef" for char in vehicle)
                    or not isinstance(values, dict)
                ):
                    raise ValueError("Invalid statistics vehicle")
                months.setdefault(vehicle, {})
                rides.setdefault(vehicle, {})
                for key, fields in values.items():
                    if not isinstance(fields, dict) or set(fields) != set(
                        model.__dataclass_fields__
                    ):
                        raise ValueError("Invalid statistics fields")
                    timestamp(fields["received_at"])
                    if model is StoredMonth:
                        previous_month(key)
                        if (
                            fields["month"] != key
                            or type(fields["revision"]) is not int
                            or fields["revision"] < 1
                        ):
                            raise ValueError("Invalid statistics month")
                        count = fields["ride_count"]
                        if count is not None and (type(count) is not int or count < 0):
                            raise ValueError("Invalid statistics count")
                        version = fields["backend_version"]
                        if version is not None and version_metadata(version) != version:
                            raise ValueError("Invalid backend version")
                        if (
                            fields["list_complete"] is not None
                            and type(fields["list_complete"]) is not bool
                        ):
                            raise ValueError("Invalid statistics coverage")
                        daily = fields["daily_distance_km"]
                        ids = fields["returned_ids"]
                        if not isinstance(daily, list) or not isinstance(ids, list):
                            raise ValueError("Invalid statistics arrays")
                        if len(daily) > 31 or len(ids) > MAX_RIDES or len(set(ids)) != len(ids):
                            raise ValueError("Invalid statistics arrays")
                        if fields["chart_status"] not in {
                            "valid",
                            "invalid",
                            "not_reported",
                            "inconsistent",
                            "total_unavailable",
                        }:
                            raise ValueError("Invalid chart status")
                        if (
                            fields["chart_status"] == "valid"
                            and len(daily) != monthrange(int(key[:4]), int(key[4:]))[1]
                        ):
                            raise ValueError("Invalid daily calendar")
                        if any(metric(value) is None for value in daily):
                            raise ValueError("Invalid daily distance")
                        if any(
                            not isinstance(value, str) or opaque_id(value) != value for value in ids
                        ):
                            raise ValueError("Invalid ride identity")
                        for field in ("distance_km", "energy_wh", "duration_s"):
                            metric(fields[field])
                        months[vehicle][key] = StoredMonth(
                            **{
                                **fields,
                                "daily_distance_km": tuple(daily),
                                "returned_ids": tuple(ids),
                            }
                        )
                    else:
                        if (
                            not isinstance(key, str)
                            or opaque_id(key) != key
                            or fields["ride_id"] != key
                        ):
                            raise ValueError("Invalid ride identity")
                        previous_month(fields["source_month"])
                        start = (
                            timestamp(fields["started_at"])
                            if fields["started_at"] is not None
                            else None
                        )
                        end = (
                            timestamp(fields["ended_at"])
                            if fields["ended_at"] is not None
                            else None
                        )
                        if start and end and start > end:
                            raise ValueError("Invalid ride time")
                        for field in ("distance_m", "energy_wh", "duration_s"):
                            metric(fields[field])
                        current = fields["current_fields"]
                        if not isinstance(current, list) or any(
                            field
                            not in {
                                "started_at",
                                "ended_at",
                                "distance_m",
                                "duration_s",
                                "energy_wh",
                            }
                            for field in current
                        ):
                            raise ValueError("Invalid metric provenance")
                        rides[vehicle][key] = StoredRide(
                            **{**fields, "current_fields": tuple(current)}
                        )
        if sum(map(len, months.values())) > MAX_MONTHS or sum(map(len, rides.values())) > MAX_RIDES:
            raise ValueError("Statistics capacity exceeded")
        self.months, self.rides = months, rides

    def update(
        self,
        sn: str,
        travel: TravelMonth,
        received: datetime,
        backend_version: str | None = None,
    ) -> bool:
        if not self.available or travel.summary is None or received.tzinfo is None:
            return False
        key = self.vehicle_key(sn)
        old = self.month(sn, travel.month)
        if old is not None and timestamp(old.received_at) >= received:
            return False
        if key not in self.months and len(self.months) >= MAX_VEHICLES:
            return False
        summary = travel.summary
        selected = {
            ride.ride_id: ride
            for ride in travel.rides
            if ride.ride_id is not None and opaque_id(ride.ride_id) == ride.ride_id
        }
        record = StoredMonth(
            travel.month,
            received.astimezone(UTC).isoformat(),
            (old.revision + 1) if old else 1,
            summary.mileage_km,
            summary.energy_wh,
            summary.ride_count,
            summary.duration_s,
            tuple(row.distance_km for row in summary.daily_mileage)
            if summary.chart_status == "valid"
            else (),
            summary.chart_status,
            summary.list_complete,
            tuple(selected)[:MAX_RIDES],
            version_metadata(backend_version),
        )
        rows = self.rides.setdefault(key, {})
        ride_changed = False
        for ride_id, ride in selected.items():
            prior = rows.get(ride_id)
            if prior is not None and timestamp(prior.received_at) >= received:
                continue
            candidate = StoredRide(
                ride_id,
                travel.month,
                record.received_at,
                ride.started_at.isoformat() if ride.started_at else None,
                ride.ended_at.isoformat() if ride.ended_at else None,
                ride.distance_m,
                ride.duration_s,
                ride.energy_raw,
                tuple(
                    name
                    for name, value in (
                        ("started_at", ride.started_at),
                        ("ended_at", ride.ended_at),
                        ("distance_m", ride.distance_m),
                        ("duration_s", ride.duration_s),
                        ("energy_wh", ride.energy_raw),
                    )
                    if value is not None
                    and not (
                        name in {"started_at", "ended_at"}
                        and {"reversed_timestamps", "conflicting_time_representations"}
                        & set(ride.issues)
                    )
                ),
            )
            if prior is not None:
                candidate = replace(
                    candidate,
                    **{
                        field: getattr(prior, field)
                        for field in (
                            "started_at",
                            "ended_at",
                            "distance_m",
                            "duration_s",
                            "energy_wh",
                        )
                        if getattr(candidate, field) is None
                    },
                )
            if prior is None or replace(candidate, received_at=prior.received_at) != prior:
                ride_changed = True
            rows[ride_id] = candidate
        # Repeated identical queries advance freshness, not the data revision.
        if (
            old is not None
            and not ride_changed
            and replace(record, received_at=old.received_at, revision=old.revision) == old
        ):
            record = replace(record, revision=old.revision)
        self.months.setdefault(key, {})[travel.month] = record
        self._trim()
        return True

    def _trim(self) -> None:
        def oldest(containers: dict[str, Any]) -> tuple[str, str]:
            return min(
                ((vehicle, key) for vehicle, values in containers.items() for key in values),
                key=lambda pair: containers[pair[0]][pair[1]].received_at,
            )

        for containers, limit in ((self.months, MAX_MONTHS), (self.rides, MAX_RIDES)):
            while sum(map(len, containers.values())) > limit:
                vehicle, key = oldest(containers)
                containers[vehicle].pop(key)
        # Include HA envelope/indentation headroom; trim metadata, never claim
        # a retained list is complete when its ride rows have been evicted.
        while len(json.dumps(self.dump(), indent=2).encode()) > MAX_BYTES - 4096:
            containers = self.rides if any(self.rides.values()) else self.months
            vehicle, key = oldest(containers)
            containers[vehicle].pop(key)

    async def async_record(
        self,
        sn: str,
        travel: TravelMonth,
        received: datetime,
        backend_version: str | None = None,
    ) -> None:
        candidate = copy(self)
        candidate.months = {key: dict(value) for key, value in self.months.items()}
        candidate.rides = {key: dict(value) for key, value in self.rides.items()}
        # Parsing/byte budgets run off-loop on an independent candidate. Readers
        # and HA's delayed-save callback never observe a half-updated ledger.
        if await self.hass.async_add_executor_job(
            candidate.update, sn, travel, received, backend_version
        ):
            self.months, self.rides = candidate.months, candidate.rides
            self._store.async_delay_save(self.dump, 5)

    async def async_save(self) -> None:
        if self.available and self.months:
            try:
                committed = self.dump()
                await self._store.async_save(committed)
                acknowledged = await self.hass.async_add_executor_job(
                    read_statistics, Path(self._store.path), self._store.key
                )
                if acknowledged != committed:
                    raise ValueError("Statistics write not acknowledged")
            except (OSError, ValueError):
                self._problem()

    async def async_remove(self) -> None:
        """Explicit account removal also removes its private history ledger."""
        await self._store.async_remove()
        self.months.clear()
        self.rides.clear()
