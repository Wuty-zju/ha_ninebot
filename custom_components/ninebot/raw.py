"""Bounded, memory-only decrypted business records, separate from HA state.

Records own immutable encoded JSON. Readers receive independent copies. Secrets
and personal profile fields are removed before retention. Diagnostics export
only approved schema names/types, never values or arbitrary upstream keys.
"""

import hashlib
import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .const import MAX_RESPONSE_BYTES

BACKEND_VERSION = "0.1.7"
MAX_NODES = 25000
MAX_DEPTH = 12
MAX_SCHEMA_PATHS = 256

# Reviewed business field names, not a generic regular-expression redactor.
# Unknown keys remain private in raw records but cannot enter diagnostics.
SCHEMA_NAMES = frozenset(
    """ai_estimate_mileage barrel_lock_status battery_exist charging dump_energy
    estimate_mileage is_common_user is_smart_service_expired left_mileage_user_choose
    loc acc lock lock_status permissions precise_estimate_mileage
    precise_mileage_user_choose pwr remain_charge_time remain_charge_timestamp
    battery_count battery_find_my_support battery_list bat_temp bms_cycle bms_volt
    electricity score battery_main battery_type charging_power charging_protection
    status have_bms_cycle_support data detail duration ec first_time list month times
    total_mileages mileages speed used_electricity start_time end_time
    start_time_format end_time_format speed_list speeds trail track points route
    support latest_support common_user_permissions index version businessType
    color is_img_special smart_service_surplus_days total_mileage vehicle_type""".split()
    + """day_total_mileage longest_distance longest_time avg_engine_power avg_shaft_speed
    avg_speed avg_throttle_opening avg_torque engine_power_nodes is_show_simple_point
    max_shaft_speed max_torque mileages_nodes shaft_speed_nodes show_simple_point_days
    speed_nodes tamp_speed_nodes throttle_opening_nodes torque_nodes""".split()
)


class Endpoint(StrEnum):
    VEHICLES = "vehicles"
    STATUS = "status"
    BATTERY = "battery"
    TRAVEL = "travel"
    TRIP_DETAIL = "trip_detail"


ENDPOINT_TEMPLATES = {
    Endpoint.VEHICLES: "ninecli --json vehicles",
    Endpoint.STATUS: "/vehicles/{sn}/status",
    Endpoint.BATTERY: "/vehicles/{sn}/battery",
    Endpoint.TRAVEL: "/vehicles/{sn}/travel?month={month}",
    Endpoint.TRIP_DETAIL: "/vehicles/{sn}/travel/{detail_id}",
}


def sensitive_key(key: str) -> bool:
    key = key.lower().replace("-", "_")
    return (
        any(word in key for word in ("token", "password", "secret", "phone", "email"))
        or key.startswith(("owner_user_", "auth_"))
        or key.endswith("_url")
        or key == "url"
        or key
        in {
            "account",
            "active_uid",
            "uid",
            "user_id",
            "nickname",
            "avatar",
            "address",
            "home_address",
            "vin",
            "and_mac",
            "ios_mac",
            "ble_name",
            "device_name",
            "authorization",
            "cookie",
            "img",
        }
    )


class RawLimitError(ValueError):
    """A record exceeds policy; carries no upstream data."""


@dataclass(frozen=True)
class SchemaField:
    path: str
    types: tuple[str, ...]
    occurrences: int


@dataclass(frozen=True)
class RawRecord:
    endpoint: Endpoint
    received_at: datetime
    query_month: str | None
    encoded: bytes
    schema: tuple[SchemaField, ...]
    shape: str
    unknown_fields: int
    redacted_fields: int
    schema_fingerprint: str
    backend_version: str = BACKEND_VERSION
    parser_contract_version: int = 1
    endpoint_version: str | None = None

    @property
    def retained_bytes(self) -> int:
        # Budget includes schema metadata as well as payload, conservatively.
        return len(self.encoded) + len(self.schema) * 512 + 512

    def payload(self) -> Any:
        return json.loads(self.encoded)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint.value,
            "source_endpoint": ENDPOINT_TEMPLATES[self.endpoint],
            "received_at": self.received_at.isoformat(),
            "query_month": self.query_month,
            "backend_version": self.backend_version,
            "parser_contract_version": self.parser_contract_version,
            "endpoint_version": self.endpoint_version,
            "shape": self.shape,
            "retained_bytes": self.retained_bytes,
            "unknown_field_count": self.unknown_fields,
            "redacted_field_count": self.redacted_fields,
            "schema_fingerprint": self.schema_fingerprint,
            "fields": [
                {"path": field.path, "types": list(field.types), "occurrences": field.occurrences}
                for field in self.schema
            ],
        }


def build_record(
    endpoint: Endpoint, payload: object, received_at: datetime, query_month: str | None = None
) -> RawRecord:
    """CPU-only preparation; callers run this outside the HA event loop."""
    if received_at.tzinfo is None:
        raise ValueError("Aware received timestamp required")
    if query_month is not None:
        if len(query_month) != 6 or not query_month.isascii() or not query_month.isdecimal():
            raise ValueError("Invalid query month")
        datetime.strptime(query_month, "%Y%m")
    received_at = received_at.astimezone(UTC)
    nodes = unknown = redacted = 0
    observed: dict[str, tuple[set[str], int]] = {}

    def visit(value: object, path: str, depth: int, approved: bool) -> Any:
        nonlocal nodes, unknown, redacted
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise RawLimitError("Record structure exceeds policy")
        kind = (
            "null"
            if value is None
            else "boolean"
            if isinstance(value, bool)
            else "number"
            if isinstance(value, (int, float))
            else "string"
            if isinstance(value, str)
            else "object"
            if isinstance(value, dict)
            else "array"
            if isinstance(value, list)
            else "invalid"
        )
        if approved and path:
            if path not in observed and len(observed) >= MAX_SCHEMA_PATHS:
                raise RawLimitError("Schema exceeds policy")
            types, count = observed.get(path, (set(), 0))
            types.add(kind)
            observed[path] = types, count + 1
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise RawLimitError("Invalid object key")
                if sensitive_key(key):
                    redacted += 1
                    result[key] = "[redacted]"
                    continue
                known = approved and key in SCHEMA_NAMES
                if not known:
                    unknown += 1
                result[key] = visit(item, f"{path}.{key}" if path else key, depth + 1, known)
            return result
        if isinstance(value, list):
            return [visit(item, f"{path}[]", depth + 1, approved) for item in value]
        if isinstance(value, str) and len(value) > MAX_RESPONSE_BYTES:
            raise RawLimitError("String exceeds policy")
        if kind == "invalid" or (isinstance(value, float) and not math.isfinite(value)):
            raise RawLimitError("Non-JSON value")
        return value

    cleaned = visit(payload, "", 0, True)
    encoded = json.dumps(
        cleaned, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise RawLimitError("Encoded record exceeds policy")
    schema = tuple(
        SchemaField(path, tuple(sorted(types)), count)
        for path, (types, count) in sorted(observed.items())
    )
    fingerprint = hashlib.sha256(
        json.dumps([(field.path, field.types) for field in schema]).encode()
    ).hexdigest()
    shape = (
        "object"
        if isinstance(payload, dict)
        else "array"
        if isinstance(payload, list)
        else "scalar"
    )
    return RawRecord(
        endpoint, received_at, query_month, encoded, schema, shape, unknown, redacted, fingerprint
    )


class RawStore:
    """Global LRU budget plus a separate detail count/TTL; no disk persistence."""

    def __init__(
        self,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        max_records: int = 128,
        max_details: int = 8,
        detail_ttl: float = 900,
    ) -> None:
        if min(max_bytes, max_records, max_details, detail_ttl) <= 0:
            raise ValueError("Positive cache limits required")
        self.max_bytes = max_bytes
        self.max_records = max_records
        self.max_details = max_details
        self.detail_ttl = detail_ttl
        self._records: OrderedDict[tuple[Endpoint, str, str, str | None], RawRecord] = OrderedDict()
        self.rejected = 0

    @property
    def retained_bytes(self) -> int:
        return sum(record.retained_bytes for record in self._records.values())

    def expire(self, now: datetime) -> None:
        for key, record in list(self._records.items()):
            if (
                key[0] is Endpoint.TRIP_DETAIL
                and (now - record.received_at).total_seconds() >= self.detail_ttl
            ):
                self._records.pop(key)

    def put(self, record: RawRecord, vehicle: str = "", detail_id: str = "") -> bool:
        self.expire(record.received_at)
        if len(vehicle) > 256 or len(detail_id) > 256 or record.retained_bytes > self.max_bytes:
            self.rejected += 1
            return False
        key = (
            record.endpoint,
            vehicle,
            detail_id,
            record.query_month if record.endpoint is Endpoint.TRIP_DETAIL else None,
        )
        self._records.pop(key, None)
        self._records[key] = record
        details = [key for key in self._records if key[0] is Endpoint.TRIP_DETAIL]
        for old in details[: -self.max_details]:
            self._records.pop(old)
        while self.retained_bytes > self.max_bytes or len(self._records) > self.max_records:
            self._records.popitem(last=False)
        return True

    def get(
        self,
        endpoint: Endpoint,
        vehicle: str,
        detail_id: str = "",
        *,
        now: datetime,
        query_month: str | None = None,
    ) -> RawRecord | None:
        self.expire(now)
        key = (
            endpoint,
            vehicle,
            detail_id,
            query_month if endpoint is Endpoint.TRIP_DETAIL else None,
        )
        record = self._records.get(key)
        if record:
            self._records.move_to_end(key)
        return record

    def discard_vehicle(self, vehicle: str) -> None:
        """Ownership disappearance/reappearance cannot revive cached telemetry."""
        for key in list(self._records):
            if key[1] == vehicle:
                self._records.pop(key)

    def diagnostics(self, now: datetime) -> dict[str, Any]:
        self.expire(now)
        return {
            "memory_only": True,
            "max_bytes": self.max_bytes,
            "retained_bytes": self.retained_bytes,
            "record_count": len(self._records),
            "rejected_records": self.rejected,
            "records": [record.diagnostics() for record in self._records.values()],
        }

    def clear(self) -> None:
        self._records.clear()
