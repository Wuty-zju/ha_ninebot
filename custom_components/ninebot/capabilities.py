"""Separate endpoint implementation from proven vehicle/control permission.

No permission bit masks are inferred from ninecli's null/opaque responses.
Until a reviewed parser contract supplies evidence, all actions fail closed.
"""

from dataclasses import dataclass
from enum import StrEnum

CONTROL_BUTTONS = (
    ("bell", "bell"),
    ("bucket", "buck"),
    ("engine_start", "engine/start"),
    ("engine_stop", "engine/stop"),
)
CONTROL_ACTIONS = frozenset(action for _, action in CONTROL_BUTTONS)
CONTROL_STATES = (
    "authentication_required",
    "disabled",
    "vehicle_not_allowed",
    "data_unavailable",
    "ready",
    "unverified",
    "denied",
    "unsupported",
)


class CapabilityState(StrEnum):
    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    DENIED = "denied"


@dataclass(frozen=True)
class ControlCapability:
    action: str
    support: CapabilityState = CapabilityState.UNKNOWN
    permission: CapabilityState = CapabilityState.UNKNOWN
    semantics_verified: bool = False
    evidence: str | None = None

    @property
    def has_evidence(self) -> bool:
        """A reviewed internal contract label, never a raw upstream value."""
        return isinstance(self.evidence, str) and bool(self.evidence.strip())

    @property
    def allowed(self) -> bool:
        return (
            self.support is CapabilityState.ALLOWED
            and self.permission is CapabilityState.ALLOWED
            and self.semantics_verified is True
            and self.has_evidence
        )


@dataclass(frozen=True)
class VehicleCapabilities:
    controls: tuple[ControlCapability, ...] = ()

    def allows(self, action: str) -> bool:
        matches = [control for control in self.controls if control.action == action]
        return action in CONTROL_ACTIONS and len(matches) == 1 and matches[0].allowed


@dataclass(frozen=True)
class ControlDecision:
    """Whitelisted policy evidence shared by control execution and diagnostics."""

    blockers: tuple[str, ...]
    support: CapabilityState
    permission: CapabilityState
    semantics_verified: bool
    evidence_available: bool
    matching_records: int

    @property
    def allowed(self) -> bool:
        return not self.blockers

    def diagnostics(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "blockers": list(self.blockers),
            "support": self.support.value,
            "permission": self.permission.value,
            "semantics_verified": self.semantics_verified,
            "evidence_available": self.evidence_available,
            "matching_records": self.matching_records,
        }


def control_state(decision: ControlDecision) -> str:
    """Small public status; never exports evidence labels or upstream payloads."""
    if decision.allowed:
        return "ready"
    blocked = set(decision.blockers)
    for reasons, state in (
        ({"authentication_required"}, "authentication_required"),
        ({"consent_missing"}, "disabled"),
        ({"vehicle_not_allowlisted"}, "vehicle_not_allowed"),
        (
            {
                "runtime_stopped",
                "vehicle_not_present",
                "profile_stale",
                "status_stale",
                "profile_query_failed",
                "status_query_failed",
            },
            "data_unavailable",
        ),
        ({"transport_unsupported", "unknown_action"}, "unsupported"),
        ({"support_denied", "permission_denied"}, "denied"),
    ):
        if blocked & reasons:
            return state
    return "unverified"


def decide_control(
    action: str,
    capabilities: VehicleCapabilities,
    checks: tuple[tuple[str, bool], ...],
) -> ControlDecision:
    """Reject unknown/duplicate capabilities without interpreting permission masks."""
    matches = [record for record in capabilities.controls if record.action == action]
    record = matches[0] if len(matches) == 1 else ControlCapability(action)
    blockers = [reason for reason, passed in checks if not passed]
    if action not in CONTROL_ACTIONS:
        blockers.append("unknown_action")
    if len(matches) > 1:
        blockers.append("ambiguous_capability")
    if record.support is not CapabilityState.ALLOWED:
        blockers.append(f"support_{record.support.value}")
    if record.permission is not CapabilityState.ALLOWED:
        blockers.append(f"permission_{record.permission.value}")
    if record.semantics_verified is not True:
        blockers.append("semantics_unverified")
    if not record.has_evidence:
        blockers.append("evidence_missing")
    return ControlDecision(
        tuple(blockers),
        record.support,
        record.permission,
        record.semantics_verified is True,
        record.has_evidence,
        len(matches),
    )
