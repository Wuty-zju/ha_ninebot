"""Only bounded integral options can alter private archive quota."""

import pytest

from custom_components.ninebot.history_capacity import history_capacity_bytes


@pytest.mark.parametrize("value", [True, False, 0, -1, 31, 2049, "2048", 100.5, None])
def test_invalid_capacity_keeps_safe_default(value):
    assert history_capacity_bytes({"history_storage_mib": value}) == 100 * 1024 * 1024


@pytest.mark.parametrize("value", [32, 100, 256, 2048])
def test_bounded_capacity_is_mebibytes(value):
    assert history_capacity_bytes({"history_storage_mib": value}) == value * 1024 * 1024
