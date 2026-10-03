"""Separate endpoint implementation from proven vehicle/control permission.

No permission bit masks are inferred from ninecli's null/opaque responses.
Until a reviewed parser contract supplies evidence, all actions fail closed.
"""

from dataclasses import dataclass
from enum import StrEnum

CONTROL_ACTIONS = frozenset({"bell", "buck", "engine/start", "engine/stop"})


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
    def allowed(self) -> bool:
        return (
            self.support is CapabilityState.ALLOWED
            and self.permission is CapabilityState.ALLOWED
            and self.semantics_verified
            and bool(self.evidence)
        )


@dataclass(frozen=True)
class VehicleCapabilities:
    controls: tuple[ControlCapability, ...] = ()

    def allows(self, action: str) -> bool:
        matches = [control for control in self.controls if control.action == action]
        return action in CONTROL_ACTIONS and len(matches) == 1 and matches[0].allowed
