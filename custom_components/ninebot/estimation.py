"""Optional SOC model. No instantaneous power or borrowed v1 accumulators."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .adapters import number
from .const import BUSINESS_TIMEZONE


@dataclass
class EnergyModel:
    """Every configuration/source change starts a new model generation."""

    voltage: float | None = None
    capacity: float | None = None
    max_range: float | None = None
    generation: int = 1
    baseline_soc: float | None = None
    sampled_at: float | None = None
    source: str = ""
    day: str = ""
    month: str = ""
    values: dict[str, float] = field(default_factory=dict)
    quality: str = "unconfigured"

    @property
    def nominal(self) -> float | None:
        if self.voltage is None or self.capacity is None:
            return None
        return self.voltage * self.capacity / 1000

    def configure(self, key: str, value: float) -> None:
        limits = {"voltage": (1, 300), "capacity": (1, 500), "max_range": (1, 5000)}
        if key not in limits or number(value, *limits[key]) is None:
            raise ValueError("Invalid model parameter")
        if getattr(self, key) == value:
            return
        setattr(self, key, value)
        if key == "max_range":
            return  # Retained legacy parameter, never used as the SOC source.
        self.generation += 1
        self.baseline_soc = self.sampled_at = None
        self.values = {}
        self.quality = "baseline_reset"

    def reset_baseline(self) -> None:
        """Keep totals, but do not bridge an interval with no observed session."""
        self.baseline_soc = self.sampled_at = None
        for key in ("delta", "out_step", "in_step"):
            self.values.pop(key, None)
        self.quality = "baseline_reset"

    def rollover(self, now: datetime) -> None:
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        day, month = local.strftime("%Y%m%d"), local.strftime("%Y%m")
        if day != self.day:
            self.values["out_daily"] = self.values["in_daily"] = 0
            self.day = day
        if month != self.month:
            self.values["out_monthly"] = self.values["in_monthly"] = 0
            self.month = month

    def sample(self, soc: float | None, now: datetime, source: str) -> None:
        """Re-baseline after gaps/jumps; quantized SOC is not a precision meter."""
        self.rollover(now)
        for key in ("delta", "out_step", "in_step"):
            self.values.pop(key, None)
        valid = number(soc, 0, 100)
        if valid is None or self.nominal is None:
            self.quality = "missing_soc" if valid is None else "unconfigured"
            return
        stamp = now.timestamp()
        if self.sampled_at is not None and stamp <= self.sampled_at:
            self.quality = "duplicate_or_old"
            return
        old, old_time = self.baseline_soc, self.sampled_at
        changed = bool(self.source and self.source != source)
        self.baseline_soc, self.sampled_at, self.source = valid, stamp, source
        if changed:
            self.generation += 1
            self.values = {}
            self.quality = "source_changed"
            return
        if old is None or old_time is None or stamp - old_time > 600:
            self.quality = "baseline_only"
            return
        difference = valid - old
        if abs(difference) > min(20, max(2, (stamp - old_time) / 30)):
            self.quality = "implausible_jump"
            return
        energy = self.nominal * difference / 100
        self.values.update(out_step=0, in_step=0)
        self.values["delta"] = energy
        direction = "out" if energy < 0 else "in"
        self.values[f"{direction}_step"] = abs(energy)
        for bucket in ("daily", "monthly", "total"):
            key = f"{direction}_{bucket}"
            self.values[key] = self.values.get(key, 0) + abs(energy)
        self.quality = "accepted"

    def dump(self) -> dict:
        """Only local model state, never a raw response or legacy energy total."""
        return asdict(self)

    @classmethod
    def restore(cls, data: object) -> "EnergyModel":
        model = cls()
        if not isinstance(data, dict):
            return model
        model.voltage = number(data.get("voltage"), 1, 300)
        model.capacity = number(data.get("capacity"), 1, 500)
        model.max_range = number(data.get("max_range"), 1, 5000)
        generation = number(data.get("generation"), 1, 1000000)
        model.generation = int(generation) if generation is not None else 1
        model.baseline_soc = number(data.get("baseline_soc"), 0, 100)
        model.sampled_at = number(data.get("sampled_at"), 0)
        for key in ("source", "day", "month"):
            value = data.get(key)
            if isinstance(value, str):
                setattr(model, key, value)
        values = data.get("values")
        if isinstance(values, dict):
            for key in (
                "out_total",
                "in_total",
                "out_daily",
                "in_daily",
                "out_monthly",
                "in_monthly",
            ):
                if (value := number(values.get(key), 0)) is not None:
                    model.values[key] = value
        return model
