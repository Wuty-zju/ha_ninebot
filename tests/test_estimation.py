from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.estimation import EnergyModel


def test_explicit_parameters_no_assumed_battery_capacity():
    model = EnergyModel()
    assert model.nominal is None
    now = datetime(2026, 10, 3, tzinfo=UTC)
    model.sample(93, now, "vehicle-soc")
    assert model.quality == "unconfigured"
    model.configure("voltage", 72)
    model.configure("capacity", 20)
    assert model.nominal == 1.44
    model.sample(93, now, "vehicle-soc")
    assert model.quality == "baseline_only"
    model.sample(92, now + timedelta(seconds=120), "vehicle-soc")
    assert model.values["out_total"] == pytest.approx(0.0144)
    model.sample(92, now + timedelta(seconds=120), "vehicle-soc")
    assert model.values["out_total"] == pytest.approx(0.0144)
    model.configure("max_range", 200)
    assert model.values["out_total"] == pytest.approx(0.0144)
    model.configure("capacity", 30)
    assert model.baseline_soc is None
    assert "out_total" not in model.values
    assert model.generation == 4


def test_gaps_jumps_source_changes_do_not_count_energy():
    model = EnergyModel(72, 20)
    now = datetime(2026, 10, 3, tzinfo=UTC)
    model.sample(93, now, "a")
    model.sample(10, now + timedelta(seconds=30), "a")
    assert model.quality == "implausible_jump"
    assert model.values["out_step"] == 0
    model.sample(90, now + timedelta(hours=2), "a")
    assert model.quality == "baseline_only"
    model.sample(50, now + timedelta(hours=2, seconds=120), "new-battery")
    assert model.quality == "source_changed"
    assert not model.values


def test_local_buckets_and_persistence():
    model = EnergyModel(72, 20)
    before = datetime(2026, 9, 30, 15, 58, tzinfo=UTC)
    model.sample(50, before, "a")
    model.sample(51, before + timedelta(seconds=60), "a")
    assert model.values["in_total"] == pytest.approx(0.0144)
    recovered = EnergyModel.restore(model.dump())
    assert recovered.baseline_soc == 51
    recovered.rollover(before + timedelta(minutes=3))
    assert recovered.month == "202610"
    assert recovered.values["in_monthly"] == 0
    assert recovered.values["in_total"] == pytest.approx(0.0144)
    assert EnergyModel.restore({"voltage": "NaN", "capacity": False}).nominal is None
    for key, value in [("unknown", 20), ("capacity", 0), ("voltage", float("nan"))]:
        with pytest.raises(ValueError):
            model.configure(key, value)
