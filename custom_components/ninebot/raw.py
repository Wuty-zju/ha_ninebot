"""Bounded, memory-only decrypted business records, separate from HA state.

Records own immutable encoded JSON. Readers receive independent copies. Secrets
and personal profile fields are removed before retention. Diagnostics export
only approved schema names/types, never values or arbitrary upstream keys.
"""

import hashlib
import hmac
import json
import math
import re
import secrets
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .const import MAX_RESPONSE_BYTES

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


class RejectReason(StrEnum):
    STRUCTURE = "structure"
    SCHEMA = "schema"
    KEY = "key"
    JSON = "json"
    SIZE = "size"
    SCOPE = "scope"
    BUDGET = "budget"


class RawLimitError(ValueError):
    """A fixed reason only; never propagate arbitrary upstream text."""

    def __init__(self, reason: RejectReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def version_metadata(value: str | None) -> str | None:
    """Only version-shaped trusted backend metadata is safe for diagnostics."""
    if value is None or len(value) > 64:
        return None
    return (
        value
        if re.fullmatch(
            r"v?\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+[a-zA-Z0-9]+(?:[.\-][a-zA-Z0-9]+)*)?",
            value,
        )
        else None
    )


@dataclass(frozen=True)
class RawReference:
    """Entry-local opaque generation; owns no identity, payload or cache entry."""

    namespace: str = field(repr=False)
    generation: int


@dataclass(frozen=True)
class SchemaDrift:
    """Only structural counts; baseline resets when its cache entry disappears."""

    changes: int = 0
    shape_changed: bool = False
    added: int = 0
    removed: int = 0
    type_changed: int = 0
    unknown_changed: bool | None = None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "change_count": self.changes,
            "shape_changed": self.shape_changed,
            "approved_paths_added": self.added,
            "approved_paths_removed": self.removed,
            "approved_types_changed": self.type_changed,
            "unknown_structure_changed": self.unknown_changed,
        }


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
    backend_version: str | None = None
    parser_contract_version: int = 1
    endpoint_version: str | None = None
    unknown_schema_complete: bool = False
    unknown_schema_fingerprint: str | None = field(default=None, repr=False)
    content_fingerprint: bytes = field(default=b"", repr=False)
    request_revision: int = 0

    @property
    def retained_bytes(self) -> int:
        # Budget includes schema metadata as well as payload, conservatively.
        return len(self.encoded) + len(self.schema) * 512 + 1024

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
            "unknown_schema_complete": self.unknown_schema_complete,
            "fields": [
                {"path": field.path, "types": list(field.types), "occurrences": field.occurrences}
                for field in self.schema
            ],
        }


def build_record(
    endpoint: Endpoint,
    payload: object,
    received_at: datetime,
    query_month: str | None = None,
    *,
    backend_version: str | None = None,
    endpoint_version: str | None = None,
    schema_salt: bytes | None = None,
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
    unknown_shapes: set[str] = set()
    unknown_complete = schema_salt is not None

    def visit(value: object, path: str, depth: int, approved: bool) -> Any:
        nonlocal nodes, unknown, redacted, unknown_complete
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise RawLimitError(RejectReason.STRUCTURE)
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
        if not approved and path and schema_salt is not None:
            # HMAC protects personal information that may occur in unknown key names.
            signature = hmac.new(schema_salt, f"{path}:{kind}".encode(), "sha256").hexdigest()
            if signature not in unknown_shapes:
                if len(unknown_shapes) < MAX_SCHEMA_PATHS:
                    unknown_shapes.add(signature)
                else:
                    unknown_complete = False
        if approved and path:
            if path not in observed and len(observed) >= MAX_SCHEMA_PATHS:
                raise RawLimitError(RejectReason.SCHEMA)
            types, count = observed.get(path, (set(), 0))
            types.add(kind)
            observed[path] = types, count + 1
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise RawLimitError(RejectReason.KEY)
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
            raise RawLimitError(RejectReason.SIZE)
        if kind == "invalid" or (isinstance(value, float) and not math.isfinite(value)):
            raise RawLimitError(RejectReason.JSON)
        return value

    cleaned = visit(payload, "", 0, True)
    encoded = json.dumps(
        cleaned, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise RawLimitError(RejectReason.SIZE)
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
        endpoint,
        received_at,
        query_month,
        encoded,
        schema,
        shape,
        unknown,
        redacted,
        fingerprint,
        backend_version=version_metadata(backend_version),
        endpoint_version=version_metadata(endpoint_version),
        unknown_schema_complete=unknown_complete,
        unknown_schema_fingerprint=(
            hashlib.sha256("".join(sorted(unknown_shapes)).encode()).hexdigest()
            if unknown_complete
            else None
        ),
        content_fingerprint=hashlib.sha256(encoded).digest(),
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
        self.reject_reasons: Counter[str] = Counter()
        self.schema_salt = secrets.token_bytes(32)
        self._namespace = secrets.token_hex(16)
        self._generation = 0
        self._generations: dict[tuple[Endpoint, str, str, str | None], int] = {}
        self._drift: dict[tuple[Endpoint, str, str, str | None], SchemaDrift] = {}

    def reject(self, reason: RejectReason) -> None:
        self.rejected += 1
        self.reject_reasons[reason.value] += 1

    def _discard(self, key: tuple[Endpoint, str, str, str | None]) -> None:
        self._records.pop(key, None)
        self._generations.pop(key, None)
        self._drift.pop(key, None)

    @property
    def retained_bytes(self) -> int:
        return sum(record.retained_bytes for record in self._records.values())

    def expire(self, now: datetime) -> None:
        for key, record in list(self._records.items()):
            if (
                key[0] is Endpoint.TRIP_DETAIL
                and (now - record.received_at).total_seconds() >= self.detail_ttl
            ):
                self._discard(key)

    def put(self, record: RawRecord, vehicle: str = "", detail_id: str = "") -> bool:
        self.expire(record.received_at)
        if len(vehicle) > 256 or len(detail_id) > 256:
            self.reject(RejectReason.SCOPE)
            return False
        if record.retained_bytes > self.max_bytes:
            self.reject(RejectReason.BUDGET)
            return False
        key = (
            record.endpoint,
            vehicle,
            detail_id,
            record.query_month if record.endpoint is Endpoint.TRIP_DETAIL else None,
        )
        before = self._records.get(key)
        drift = SchemaDrift()
        if before is not None:
            old = {item.path: item.types for item in before.schema}
            new = {item.path: item.types for item in record.schema}
            unknown_changed = (
                before.unknown_schema_fingerprint != record.unknown_schema_fingerprint
                if before.unknown_schema_complete and record.unknown_schema_complete
                else None
            )
            added, removed = len(new.keys() - old.keys()), len(old.keys() - new.keys())
            types = sum(old[path] != new[path] for path in old.keys() & new.keys())
            shape = before.shape != record.shape
            changed = shape or bool(added or removed or types) or unknown_changed is True
            drift = SchemaDrift(
                self._drift[key].changes + int(changed),
                shape,
                added,
                removed,
                types,
                unknown_changed,
            )
        self._records.pop(key, None)
        self._records[key] = record
        self._generation += 1
        self._generations[key] = self._generation
        self._drift[key] = drift
        details = [key for key in self._records if key[0] is Endpoint.TRIP_DETAIL]
        for old_key in details[: -self.max_details]:
            self._discard(old_key)
        while self.retained_bytes > self.max_bytes or len(self._records) > self.max_records:
            self._discard(next(iter(self._records)))
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

    def reference(
        self,
        endpoint: Endpoint,
        vehicle: str,
        detail_id: str = "",
        *,
        now: datetime,
        query_month: str | None = None,
    ) -> RawReference | None:
        if self.get(endpoint, vehicle, detail_id, now=now, query_month=query_month) is None:
            return None
        key = (
            endpoint,
            vehicle,
            detail_id,
            query_month if endpoint is Endpoint.TRIP_DETAIL else None,
        )
        return RawReference(self._namespace, self._generations[key])

    def resolve(self, reference: RawReference, *, now: datetime) -> RawRecord | None:
        self.expire(now)
        if reference.namespace != self._namespace:
            return None
        for key, generation in self._generations.items():
            if generation == reference.generation:
                return self._records[key]
        return None

    def discard_vehicle(self, vehicle: str) -> None:
        """Ownership disappearance/reappearance cannot revive cached telemetry."""
        for key in list(self._records):
            if key[1] == vehicle:
                self._discard(key)

    def vehicle_summary(self, vehicle: str, now: datetime) -> dict[str, int]:
        """Only bounded numeric metadata, never schema keys or payload values.

        Account-wide vehicle discovery is excluded. Path counts are distinct
        per endpoint across retained records; unknown/redacted counts are field
        occurrences. A nonzero cache count is not proof of fresh cloud data.
        """
        self.expire(now)
        records = [record for key, record in self._records.items() if key[1] == vehicle]
        return {
            "record_count": len(records),
            "schema_path_count": len(
                {(record.endpoint, field.path) for record in records for field in record.schema}
            ),
            "unknown_field_count": sum(record.unknown_fields for record in records),
            "redacted_field_count": sum(record.redacted_fields for record in records),
            "retained_bytes": sum(record.retained_bytes for record in records),
        }

    def diagnostics(self, now: datetime) -> dict[str, Any]:
        self.expire(now)
        return {
            "memory_only": True,
            "max_bytes": self.max_bytes,
            "retained_bytes": self.retained_bytes,
            "record_count": len(self._records),
            "rejected_records": self.rejected,
            "rejection_reasons": dict(self.reject_reasons),
            "records": [
                {**record.diagnostics(), "schema_drift": self._drift[key].diagnostics()}
                for key, record in self._records.items()
            ],
        }

    def clear(self) -> None:
        self._records.clear()
        self._generations.clear()
        self._drift.clear()
