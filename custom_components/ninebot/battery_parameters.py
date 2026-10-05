"""User-supplied rated specifications, never SOC-derived energy counters."""

from dataclasses import asdict, dataclass

from .parsing import number


@dataclass(frozen=True)
class BatteryParameters:
    voltage: float | None = None
    capacity: float | None = None

    @property
    def nominal(self) -> float | None:
        """Rated V × Ah in kWh, not measured capacity or charge consumption."""
        if self.voltage is None or self.capacity is None:
            return None
        return self.voltage * self.capacity / 1000

    def dump(self) -> dict[str, float | None]:
        return asdict(self)

    @classmethod
    def restore(cls, data: object) -> "BatteryParameters":
        if not isinstance(data, dict):
            return cls()
        return cls(number(data.get("voltage"), 1, 300), number(data.get("capacity"), 1, 500))
