"""Evidence-based transitions between successful, ordered status observations."""

from dataclasses import dataclass
from datetime import datetime

TRANSITIONS = (
    ("powered_off", "powered_on"),
    ("charging_stopped", "charging_started"),
    ("unlocked", "locked"),
    ("seat_unlocked", "seat_locked"),
)


@dataclass(frozen=True)
class StatusObservation:
    identity: tuple[int, int, int]  # runtime generation, vehicle ownership, receipt revision
    received_at: datetime
    values: tuple[bool | None, bool | None, bool | None, bool | None]


@dataclass
class StatusEventCursor:
    """A runtime-only baseline: reconnection is never a history replay."""

    previous: StatusObservation | None = None

    def observe(self, sample: StatusObservation | None, *, max_gap_s: int) -> tuple[str, ...]:
        previous = self.previous
        if sample is None:
            self.previous = None
            return ()
        if previous is not None and sample.identity == previous.identity:
            return ()  # Battery/profile updates cannot reuse a status receipt.
        self.previous = sample
        if (
            previous is None
            or sample.identity[:2] != previous.identity[:2]
            or sample.identity[2] <= previous.identity[2]
            or not 0 <= (sample.received_at - previous.received_at).total_seconds() <= max_gap_s
        ):
            return ()
        return tuple(
            TRANSITIONS[index][int(current)]
            for index, (old, current) in enumerate(zip(previous.values, sample.values, strict=True))
            if type(old) is bool and type(current) is bool and old != current
        )
