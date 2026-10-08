"""Bounded command observations, separate from permission and physical effects."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .capabilities import CONTROL_ACTIONS
from .exceptions import ErrorKind

MAX_CONTROL_RESULTS = 64


class CommandOutcome(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    AUTH_REQUIRED = "authentication_required"
    CANCELLED = "cancelled"
    ALREADY_IN_TARGET = "already_in_target"
    REJECTED = "rejected"


class ReadbackOutcome(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    REFRESHED = "refreshed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class Confirmation(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    OBSERVED_TARGET_COMMAND_UNCERTAIN = "observed_target_command_uncertain"
    ALREADY_IN_TARGET = "already_in_target"
    TARGET_NOT_OBSERVED = "target_not_observed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ControlResult:
    """One local backend invocation; acceptance is not physical completion."""

    attempted_at: datetime
    outcome: CommandOutcome = CommandOutcome.PENDING
    error: ErrorKind | None = None
    readback: ReadbackOutcome = ReadbackOutcome.NOT_REQUESTED
    readback_error: ErrorKind | None = None
    finished_at: datetime | None = None
    target_locked: bool | None = None
    observed_locked: bool | None = None
    confirmation: Confirmation = Confirmation.NOT_REQUESTED
    read_attempts: int = 0

    def diagnostics(self) -> dict[str, str | bool | int | None]:
        return {
            "attempted_at": self.attempted_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "outcome": self.outcome.value,
            "error": self.error.value if self.error else None,
            "readback": self.readback.value,
            "readback_error": self.readback_error.value if self.readback_error else None,
            "physical_outcome_verified": False,
            "target_locked": self.target_locked,
            "observed_locked": self.observed_locked,
            "confirmation": self.confirmation.value,
            "read_attempts": self.read_attempts,
        }


class ControlResults:
    """Latest observation per vehicle/action, with a strict global record bound.

    Each invocation owns its own object. Finishing an older invocation cannot
    overwrite the newer object's slot, even if readbacks complete out of order.
    No response values or error text are stored, and nothing is persisted.
    """

    def __init__(self) -> None:
        self._records: OrderedDict[tuple[str, str], ControlResult] = OrderedDict()

    def start(self, vehicle: str, action: str, now: datetime) -> ControlResult:
        if action not in CONTROL_ACTIONS:
            raise ValueError("Unknown control action")
        key = (vehicle, action)
        self._records.pop(key, None)
        result = self._records[key] = ControlResult(now)
        while len(self._records) > MAX_CONTROL_RESULTS:
            self._records.popitem(last=False)
        return result

    def diagnostics(self, vehicle: str) -> dict[str, dict[str, str | bool | int | None]]:
        return {
            action: result.diagnostics()
            for action in sorted(CONTROL_ACTIONS)
            if (result := self._records.get((vehicle, action))) is not None
        }

    def clear(self) -> None:
        self._records.clear()
