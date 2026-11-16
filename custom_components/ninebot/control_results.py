"""Bounded command observations, separate from permission and physical effects."""

import logging
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .capabilities import CONTROL_ACTIONS
from .exceptions import ErrorKind, FailureContext

MAX_CONTROL_RESULTS = 64
LOGGER = logging.getLogger(__name__)


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
    SUPERSEDED = "superseded"
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
    support_id: str = field(default_factory=lambda: secrets.token_hex(4))
    blocked_reason: str | None = None
    failure_context: FailureContext = field(default_factory=FailureContext)

    def diagnostics(self) -> dict[str, str | bool | int | None]:
        return {
            "support_id": self.support_id,
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
            "blocked_reason": self.blocked_reason,
            **self.failure_context.diagnostics(),
        }

    def log(self, action: str) -> None:
        """One result line per user invocation; never log vehicle or error text."""
        if action not in CONTROL_ACTIONS:
            return
        elapsed = (
            max(0, int((self.finished_at - self.attempted_at).total_seconds() * 1000))
            if self.finished_at
            else None
        )
        LOGGER.debug(
            "Control result (support=%s, action=%s, outcome=%s, blocked_reason=%s, "
            "error=%s, readback=%s, confirmation=%s, reads=%s, elapsed_ms=%s, "
            "endpoint=%s, proxy_http_status=%s, envelope_code=%s)",
            self.support_id,
            action,
            self.outcome.value,
            self.blocked_reason,
            self.error.value if self.error else None,
            self.readback.value,
            self.confirmation.value,
            self.read_attempts,
            elapsed,
            self.failure_context.endpoint.value,
            self.failure_context.proxy_http_status,
            self.failure_context.envelope_code.value
            if self.failure_context.envelope_code
            else None,
        )


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

    def latest(
        self, vehicle: str, actions: tuple[str, ...]
    ) -> dict[str, str | bool | int | None] | None:
        """Insertion order stays correct even when rapid attempts share a timestamp."""
        return next(
            (
                result.diagnostics()
                for (sn, action), result in reversed(self._records.items())
                if sn == vehicle and action in actions
            ),
            None,
        )

    def clear(self) -> None:
        self._records.clear()
