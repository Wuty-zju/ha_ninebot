"""Stable time ranges for event summaries and calendar descriptions."""

from datetime import UTC, datetime, timedelta, timezone

from custom_components.ninebot.ride_presentation import ride_time_range


def test_same_day_offset_and_cross_midnight_are_unambiguous():
    start = datetime(2026, 10, 8, 16, 21, 34, tzinfo=timezone(timedelta(hours=8)))
    end = start + timedelta(seconds=1906)
    assert ride_time_range(start, end) == "2026-10-08T16:21:34+08:00 – 16:53:20+08:00"
    midnight = start.replace(hour=23, minute=59)
    assert "2026-10-09" in ride_time_range(midnight, midnight + timedelta(minutes=2))
    assert ride_time_range(None, end).startswith("— – 2026-10-08")
    assert ride_time_range(start, None).endswith(" – —")


def test_invalid_and_offset_change_do_not_hide_missing_or_ambiguous_times():
    stamp = datetime(2026, 10, 8, tzinfo=UTC)
    assert ride_time_range(None, None) is None
    assert ride_time_range(stamp, stamp - timedelta(seconds=1)) is None
    assert ride_time_range(stamp.replace(tzinfo=None), stamp) is None
    different_offset = (stamp + timedelta(hours=1)).astimezone(timezone(timedelta(hours=1)))
    assert different_offset.isoformat() in ride_time_range(stamp, different_offset)
