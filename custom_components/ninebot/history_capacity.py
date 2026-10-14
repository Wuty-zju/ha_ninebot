"""Bounded account archive options; malformed legacy values retain safe defaults."""

from collections.abc import Mapping

CONF_HISTORY_CAPACITY = "history_storage_mib"
DEFAULT_HISTORY_MIB = 100
MIN_HISTORY_MIB = 32
MAX_HISTORY_MIB = 2048


def history_capacity_mib(options: Mapping[str, object]) -> int:
    value = options.get(CONF_HISTORY_CAPACITY, DEFAULT_HISTORY_MIB)
    return (
        value
        if type(value) is int and MIN_HISTORY_MIB <= value <= MAX_HISTORY_MIB
        else DEFAULT_HISTORY_MIB
    )


def history_capacity_bytes(options: Mapping[str, object]) -> int:
    return history_capacity_mib(options) * 1024 * 1024
