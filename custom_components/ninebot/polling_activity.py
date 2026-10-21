"""Bounded scheduling hints from verified status receipts, never physical events."""

from dataclasses import dataclass, field

from .polling_policy import PollingPolicy

STOP_READ_OFFSETS = (15, 45, 90, 180, 300, 600)


@dataclass
class VehicleActivity:
    revision: int = 0
    observed_at: float | None = None
    charging: bool | None = None
    active: bool | None = None
    locked: bool | None = None
    leases: dict[str, float] = field(default_factory=dict)
    stopped_at: float | None = None
    closing: tuple[float, ...] = ()

    def observe(
        self,
        policy: PollingPolicy,
        *,
        now: float,
        observed_at: float,
        revision: int,
        charging: bool | None,
        active: bool | None,
        locked: bool | None,
        own_control: bool = False,
    ) -> frozenset[str]:
        """Only newer, timely receipts update a baseline or open a finite window."""
        if (
            revision <= self.revision
            or not 0 <= now - observed_at <= policy.max_age("status")
            or (self.observed_at is not None and observed_at < self.observed_at)
        ):
            return frozenset()
        previous = self.observed_at
        continuous = previous is not None and now - previous <= policy.max_age("status")
        due: set[str] = set()
        if not continuous:
            self.leases.clear()
            self.closing = ()
            self.stopped_at = None
        else:
            if charging is not None and self.charging is not None and charging != self.charging:
                if self._lease("charge_transition", now, 120):
                    due.add("battery")
            if self.locked is True and locked is False and not own_control:
                if self._lease("recent_unlock", now, 60):
                    due.add("battery")
            if self.active is True and (
                active is False or (self.locked is False and locked is True)
            ):
                self._lease("recent_stop", now, 120)
                if self.stopped_at is None or now >= self.stopped_at + 600:
                    self.stopped_at = now
                    self.closing = tuple(now + offset for offset in STOP_READ_OFFSETS)
        self.revision = revision
        self.observed_at = observed_at
        self.charging, self.active, self.locked = charging, active, locked
        return frozenset(due)

    def _lease(self, name: str, now: float, seconds: float) -> bool:
        if self.leases.get(name, 0) > now:
            return False  # Repeated edges do not extend an existing window.
        self.leases[name] = now + seconds
        return True

    def reasons(self, policy: PollingPolicy, now: float) -> tuple[str, ...]:
        if self.observed_at is None:
            return ()
        reasons = [name for name, expiry in self.leases.items() if now < expiry]
        if 0 <= now - self.observed_at <= policy.max_age("status"):
            if self.charging is True:
                reasons.append("charging")
            if self.active is True:
                reasons.append("active")
        return tuple(sorted(reasons))

    def interval(self, policy: PollingPolicy, group: str, now: float) -> int:
        return min(
            (policy.scene_interval(group, reason) for reason in self.reasons(policy, now)),
            default=policy.interval(group),
        )

    def closing_deadline(self, now: float) -> float | None:
        if self.stopped_at is None or now > self.stopped_at + 600:
            return None
        return self.closing[0] if self.closing else None

    def travel_observed(self, received_at: float, now: float) -> None:
        if self.stopped_at is not None and received_at >= self.stopped_at:
            self.closing = tuple(deadline for deadline in self.closing if deadline > now)

    def diagnostics(self, policy: PollingPolicy, now: float) -> dict[str, object]:
        return {
            "reasons": list(self.reasons(policy, now)),
            "leases_remaining_seconds": {
                name: max(0, expiry - now) for name, expiry in self.leases.items() if expiry > now
            },
            "closing_reads_remaining": len(self.closing)
            if self.closing_deadline(now) is not None
            else 0,
        }


@dataclass
class PollBudget:
    """Local periodic-read admission, independent of any upstream rate limit."""

    rate: float
    capacity: float
    tokens: float | None = None
    updated_at: float | None = None

    def delay(self, now: float) -> float:
        if self.updated_at is None:
            self.tokens = self.capacity
        else:
            self.tokens = min(
                self.capacity, (self.tokens or 0) + max(0, now - self.updated_at) * self.rate
            )
        self.updated_at = now
        return max(0, (1 - (self.tokens or 0)) / self.rate)

    def take(self, now: float) -> None:
        if self.delay(now) > 0:
            raise ValueError("Periodic request requires an admission slot")
        self.tokens = (self.tokens or 0) - 1
