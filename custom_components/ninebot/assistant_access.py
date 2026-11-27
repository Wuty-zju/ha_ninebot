"""Versioned Assistant consent and its conservative rollback projection."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

CONF_ASSISTANT_ACCESS = "assistant_access"
CONF_ASSISTANT = "enable_assistant"
CONF_ASSISTANT_VEHICLES = "assistant_vehicles"
CONF_ASSISTANT_TASKS = "enable_assistant_tasks"

ACCESS_VERSION = 1
_LEGACY_PROJECTION_WRITTEN = "legacy_projection_written"

type ReadScope = Literal["status", "statistics", "trips", "sync"]
READ_STATUS: Literal["status"] = "status"
READ_STATISTICS: Literal["statistics"] = "statistics"
READ_TRIPS: Literal["trips"] = "trips"
READ_SYNC: Literal["sync"] = "sync"
READ_SCOPES = (READ_STATUS, READ_STATISTICS, READ_TRIPS, READ_SYNC)
ALL_READS = frozenset[ReadScope](READ_SCOPES)

type TaskAccess = Literal["off", "preview", "request"]
TASK_OFF: Literal["off"] = "off"
TASK_PREVIEW: Literal["preview"] = "preview"
TASK_REQUEST: Literal["request"] = "request"
TASK_LEVELS = (TASK_OFF, TASK_PREVIEW, TASK_REQUEST)


@dataclass(frozen=True)
class VehicleAccess:
    reads: frozenset[ReadScope] = frozenset()
    tasks: TaskAccess = TASK_OFF


@dataclass(frozen=True)
class AssistantAccess:
    enabled: bool = False
    vehicles: Mapping[str, VehicleAccess] = field(default_factory=lambda: MappingProxyType({}))
    pending_confirmation: bool = False
    legacy_task_vehicles: frozenset[str] = frozenset()

    @property
    def durable_task_vehicles(self) -> frozenset[str]:
        """Vehicles whose existing tickets can still pass the consent check."""
        if not self.enabled:
            return frozenset()
        if self.pending_confirmation:
            return self.legacy_task_vehicles.intersection(self.vehicles)
        return frozenset(sn for sn, access in self.vehicles.items() if access.tasks == TASK_REQUEST)


def _vehicle_serials(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(sn for sn in value if isinstance(sn, str) and sn.strip())


def _legacy_access(options: Mapping[str, Any]) -> AssistantAccess:
    enabled = options.get(CONF_ASSISTANT)
    if type(enabled) is not bool:
        return AssistantAccess()
    serials = _vehicle_serials(options.get(CONF_ASSISTANT_VEHICLES))
    legacy_tasks = serials if enabled and options.get(CONF_ASSISTANT_TASKS) is True else frozenset()
    return AssistantAccess(
        enabled,
        MappingProxyType({sn: VehicleAccess(ALL_READS) for sn in sorted(serials)}),
        bool(legacy_tasks),
        legacy_tasks,
    )


def _legacy_projection(access: AssistantAccess) -> dict[str, Any]:
    serials = sorted(
        sn
        for sn, vehicle in access.vehicles.items()
        if access.enabled and vehicle.reads == ALL_READS
    )
    return {
        CONF_ASSISTANT: bool(serials),
        CONF_ASSISTANT_VEHICLES: serials,
        CONF_ASSISTANT_TASKS: bool(serials)
        and not access.pending_confirmation
        and all(access.vehicles[sn].tasks == TASK_REQUEST for sn in serials),
    }


def _legacy_projection_changed(options: Mapping[str, Any], access: AssistantAccess) -> bool:
    expected = _legacy_projection(access)
    serials = options.get(CONF_ASSISTANT_VEHICLES)
    return (
        options.get(CONF_ASSISTANT) is not expected[CONF_ASSISTANT]
        or options.get(CONF_ASSISTANT_TASKS) is not expected[CONF_ASSISTANT_TASKS]
        or not isinstance(serials, list)
        or any(not isinstance(sn, str) or not sn.strip() for sn in serials)
        or set(serials) != set(expected[CONF_ASSISTANT_VEHICLES])
    )


def _known_access(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and type(version := value.get("version")) is int
        and version == ACCESS_VERSION
    )


def assistant_access_editable(options: Mapping[str, Any]) -> bool:
    """Preserve an access document whose version this integration cannot edit."""
    return CONF_ASSISTANT_ACCESS not in options or _known_access(options[CONF_ASSISTANT_ACCESS])


def parse_assistant_access(options: Mapping[str, Any]) -> AssistantAccess:
    """Read effective consent from current, legacy or rolled-back options."""
    if CONF_ASSISTANT_ACCESS not in options:
        return _legacy_access(options)
    raw = options[CONF_ASSISTANT_ACCESS]
    if not _known_access(raw):
        return AssistantAccess()

    enabled = raw.get("enabled") is True
    awaiting_confirmation = raw.get("pending_confirmation") is True
    pending = enabled and awaiting_confirmation
    vehicles = {}
    if isinstance(values := raw.get("vehicles"), Mapping):
        for sn, vehicle in values.items():
            if not isinstance(sn, str) or not sn.strip() or not isinstance(vehicle, Mapping):
                continue
            reads = vehicle.get("reads")
            tasks = vehicle.get("tasks")
            vehicles[sn] = VehicleAccess(
                frozenset(
                    scope for scope in READ_SCOPES if isinstance(reads, list) and scope in reads
                ),
                next(
                    (
                        level
                        for level in TASK_LEVELS
                        if not awaiting_confirmation and level == tasks
                    ),
                    TASK_OFF,
                ),
            )
    legacy_tasks = (
        _vehicle_serials(raw.get("legacy_task_vehicles")).intersection(vehicles)
        if pending
        else frozenset()
    )
    access = AssistantAccess(enabled, MappingProxyType(vehicles), pending, legacy_tasks)
    if raw.get(_LEGACY_PROJECTION_WRITTEN) is True and _legacy_projection_changed(options, access):
        # A downgrade can edit the old account-wide settings. Take the current
        # legacy consent instead of resurrecting the previous versioned grants.
        return _legacy_access(options)
    return access


def with_assistant_access(options: Mapping[str, Any], access: AssistantAccess) -> dict[str, Any]:
    """Store an explicit edit and keep old-version permissions within its bounds."""
    pending = access.enabled and access.pending_confirmation
    return {
        **options,
        **_legacy_projection(access),
        CONF_ASSISTANT_ACCESS: {
            "version": ACCESS_VERSION,
            "enabled": access.enabled,
            "vehicles": {
                sn: {
                    "reads": [scope for scope in READ_SCOPES if scope in vehicle.reads],
                    "tasks": TASK_OFF if access.pending_confirmation else vehicle.tasks,
                }
                for sn, vehicle in sorted(access.vehicles.items())
            },
            "pending_confirmation": pending,
            "legacy_task_vehicles": sorted(access.durable_task_vehicles) if pending else [],
            _LEGACY_PROJECTION_WRITTEN: True,
        },
    }


def normalize_assistant_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """Persist effective consent once, preserving unrelated and future-version data."""
    access = parse_assistant_access(options)
    if not assistant_access_editable(options):
        return {**options, **_legacy_projection(access)}
    return with_assistant_access(options, access)
