"""Shared background-request policy; no network or HA fixture required."""

import pytest

from custom_components.ninebot.history_budget import configured_budget, valid_budget


@pytest.mark.parametrize("value", [1, 30, 60, 120])
def test_explicit_budget_is_preserved(value):
    assert valid_budget(value)
    assert configured_budget({"history_requests_per_hour": value}) == value


@pytest.mark.parametrize("value", [True, False, 0, 121, 60.5, "120", None])
def test_invalid_budget_does_not_bypass_limits(value):
    assert not valid_budget(value)
    assert configured_budget({"history_requests_per_hour": value}) == 60


def test_missing_budget_uses_sixty_without_mutating_options():
    options = {}
    assert configured_budget(options) == 60
    assert options == {}
