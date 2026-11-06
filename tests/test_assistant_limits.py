"""Read admission is bounded, independent per user, and released on cancellation."""

import asyncio

import pytest

from custom_components.ninebot.assistant_limits import (
    DATA_KEY,
    MAX_USERS,
    ReadLimitExceeded,
    ReadLimits,
    read_slot,
)


def test_rolling_user_windows_are_independent_and_expire_at_the_boundary():
    limits = ReadLimits()
    for _ in range(20):
        limits.acquire("first", 0)
        limits.release()
    with pytest.raises(ReadLimitExceeded) as caught:
        limits.acquire("first", 59.5)
    assert caught.value.retry_after == 1
    limits.acquire("second", 59.5)
    limits.release()
    limits.acquire("first", 60)
    assert len(limits.calls["first"]) == 1
    limits.release()


def test_two_inflight_calls_are_an_api_wide_bound():
    limits = ReadLimits()
    limits.acquire("first", 0)
    limits.acquire("second", 0)
    with pytest.raises(ReadLimitExceeded):
        limits.acquire("third", 0)
    assert "third" not in limits.calls
    limits.release()
    limits.acquire("third", 0)
    assert limits.active == 2
    limits.release()
    limits.release()


def test_unknown_users_cannot_evict_unexpired_rate_windows():
    limits = ReadLimits()
    for index in range(MAX_USERS):
        limits.acquire(str(index), 0)
        limits.release()
    with pytest.raises(ReadLimitExceeded):
        limits.acquire("extra", 1)
    assert len(limits.calls) == MAX_USERS
    limits.acquire("extra", 60)
    limits.release()
    assert len(limits.calls) == 1


async def test_cancellation_and_errors_release_slots_without_background_tasks(hass):
    assert DATA_KEY not in hass.data
    with pytest.raises(asyncio.CancelledError):
        with read_slot(hass, "synthetic"):
            raise asyncio.CancelledError
    assert hass.data[DATA_KEY].active == 0
    with pytest.raises(ValueError):
        with read_slot(hass, "synthetic"):
            raise ValueError("synthetic")
    assert hass.data[DATA_KEY].active == 0
    assert len(hass.data[DATA_KEY].calls["synthetic"]) == 2
