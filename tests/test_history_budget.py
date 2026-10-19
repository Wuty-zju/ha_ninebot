"""Shared background-request policy; no network or HA fixture required."""

import pytest

from custom_components.ninebot.history_budget import (
    configured_budget,
    request_interval,
    valid_budget,
)


@pytest.mark.parametrize("value", [0, 1, 30, 60, 120])
def test_explicit_budget_is_preserved(value):
    assert valid_budget(value)
    assert configured_budget({"history_requests_per_hour": value}) == value


@pytest.mark.parametrize("value", [True, False, -1, 121, 60.5, "120", None])
def test_invalid_budget_does_not_bypass_limits(value):
    assert not valid_budget(value)
    assert configured_budget({"history_requests_per_hour": value}) == 60


def test_missing_budget_uses_sixty_without_mutating_options():
    options = {}
    assert configured_budget(options) == 60
    assert options == {}


@pytest.mark.parametrize(("budget", "interval"), [(0, 0), (1, 3600), (60, 60), (120, 30)])
def test_request_interval_keeps_zero_distinct_from_default(budget, interval):
    assert request_interval(budget) == interval


@pytest.mark.parametrize("value", [True, -1, 121, 60.5, "0", None])
def test_request_interval_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        request_interval(value)
