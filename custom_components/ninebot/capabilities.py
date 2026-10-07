"""Separate local command readiness from cloud authorization and physical effect.

Explicit opt-in permits known commands to reach the cloud for final authorization.
Unknown permissions remain unknown; reviewed denials and ambiguous evidence block.
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
    "parking_unverified",
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
        """Fully reviewed capability evidence, distinct from dispatch readiness."""
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
    """Local dispatch policy shared by execution and diagnostics, not cloud consent."""

    blockers: tuple[str, ...]
    support: CapabilityState
    permission: CapabilityState
    semantics_verified: bool
    evidence_available: bool
    matching_records: int

    @property
    def allowed(self) -> bool:
        """The command may be sent once; the cloud still decides authorization."""
        return not self.blockers

    def diagnostics(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "dispatch_policy": "cloud_authorization",
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
        ({"parking_unverified"}, "parking_unverified"),
    ):
        if blocked & reasons:
            return state
    return "unverified"


def decide_control(
    action: str,
    capabilities: VehicleCapabilities,
    checks: tuple[tuple[str, bool], ...],
) -> ControlDecision:
    """Permit cloud authorization after local checks; never override known denials."""
    matches = [record for record in capabilities.controls if record.action == action]
    record = matches[0] if len(matches) == 1 else ControlCapability(action)
    blockers = [reason for reason, passed in checks if not passed]
    if action not in CONTROL_ACTIONS:
        blockers.append("unknown_action")
    if len(matches) > 1:
        blockers.append("ambiguous_capability")
    if record.support is CapabilityState.DENIED:
        blockers.append("support_denied")
    if record.permission is CapabilityState.DENIED:
        blockers.append("permission_denied")
    return ControlDecision(
        tuple(blockers),
        record.support,
        record.permission,
        record.semantics_verified is True,
        record.has_evidence,
        len(matches),
    )
