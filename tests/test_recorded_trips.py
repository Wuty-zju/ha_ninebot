"""Actual local archive pages and HA responses; no cloud/vehicle access."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol
from homeassistant.core import Context, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import archive_browse
from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_browse import RecordedCursors
from custom_components.ninebot.recorded_actions import RECORDED_SCHEMA, business_date
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)
START = NOW - timedelta(days=1)
SN = "SyntheticSN"


def sample(ids=("first", "second", "third"), *, count=None, month="202610", start=START):
    return travel(
        {
            "times": len(ids) if count is None else count,
            "list": [
                {
                    "travel_id": identity,
                    "start_time": int(start.timestamp()),
                    "end_time": int(start.timestamp()) + 600,
                    "mileages": "1.20",
                    "duration": 600,
                    "speed": "30.0",
                    "ec": 100,
                }
                for identity in ids
            ],
        },
        month,
    )


@pytest.fixture
async def archive(tmp_path):
    actor = RideArchive(tmp_path / "recorded.sqlite3", "a" * 64)
    await actor.async_open()
    try:
        yield actor
    finally:
        await actor.async_close()


async def page(archive, *, limit=2, cursor=None, now=NOW):
    return await archive.async_recorded_page(
        SN, START - timedelta(days=1), NOW + timedelta(days=1), now, limit=limit, cursor=cursor
    )


async def test_real_keyset_pages_same_timestamp_precision_receipt_and_no_writes(archive):
    await archive.async_record_month(SN, sample(ids=tuple(f"ride-{i:03}" for i in range(205))), NOW)
    original = archive.path.read_bytes()
    first = await page(archive, limit=100)
    second = await page(archive, limit=100, cursor=first.next_cursor)
    last = await page(archive, limit=100, cursor=second.next_cursor)
    assert not last.next_cursor and len(last.records) == 5
    assert [row.ride.ride_id for row in (*first.records, *second.records, *last.records)] == [
        f"ride-{i:03}" for i in range(205)
    ]
    assert dict(first.records[0].ride.precision)["mileages"] == 2
    assert all(row.received_at == NOW for row in first.records)
    assert first.months[0].list_complete is True
    assert first.months[0].reported_count == first.months[0].returned_count == 205
    assert archive.path.read_bytes() == original
    other = await archive.async_recorded_page("foreign", START, NOW, NOW)
    assert not other.records and not other.months


async def test_latest_complete_membership_partial_retention_and_cross_month_dedup(archive):
    await archive.async_record_month(SN, sample(), NOW)
    await archive.async_record_month(
        SN, sample(ids=("first",), count=3), NOW + timedelta(seconds=1)
    )
    assert len((await page(archive, limit=10)).records) == 3
    await archive.async_record_month(SN, sample(ids=("first",)), NOW + timedelta(seconds=2))
    assert len((await page(archive, limit=10)).records) == 1
    await archive.async_record_month(SN, sample(ids=("first",), month="202609"), NOW)
    assert len((await page(archive, limit=10)).records) == 1
    await archive.async_record_month(SN, sample(ids=()), NOW + timedelta(seconds=3))
    assert len((await page(archive, limit=10)).records) == 1
    await archive.async_record_month(SN, sample(ids=(), month="202609"), NOW + timedelta(seconds=4))
    assert not (await page(archive)).records
    assert len((await archive.async_page(SN)).rides) == 3  # Evidence was not deleted.


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", "b" * 64),
        ("vehicle", "other"),
        ("revision", -1),
        ("start", "wrong"),
        ("end", "wrong"),
    ],
)
async def test_cursor_scope_and_revision_rejected(archive, field, value):
    await archive.async_record_month(SN, sample(), NOW)
    first = await page(archive)
    with pytest.raises(ArchiveError) as error:
        await page(archive, cursor=replace(first.next_cursor, **{field: value}))
    assert error.value.kind is ArchiveFailure.CURSOR


async def test_frozen_as_of_and_conflicting_time_skip_are_not_invented(archive):
    await archive.async_record_month(SN, sample(), NOW)
    first = await page(archive, limit=1)
    # Ongoing/future rows were not selected, even when the caller's clock advances.
    await archive.async_record_month("other", sample(start=NOW), NOW)
    with pytest.raises(ArchiveError):
        await page(archive, cursor=first.next_cursor)
    first = await page(archive, limit=1)
    final = await page(archive, limit=10, cursor=first.next_cursor, now=NOW + timedelta(days=1))
    assert final.next_cursor is None and len(final.records) == 2
    await archive.async_record_month(
        SN, sample(ids=("future",), start=NOW), NOW + timedelta(seconds=1)
    )
    assert not (await page(archive, limit=10)).records
    bad = replace(sample(ids=("bad",)).rides[0], issues=("conflicting_time_representations",))
    await archive.async_record_month(
        SN, replace(sample(ids=("bad",)), rides=(bad,)), NOW + timedelta(seconds=2)
    )
    result = await page(archive)
    assert result.skipped_conflicting_times == 1 and not result.records


@pytest.mark.parametrize("budget,value", [("MAX_TIMELINE_RIDES", 1), ("MAX_TIMELINE_BYTES", 1)])
async def test_scan_budget_is_explicit_and_never_pauses_writes(archive, monkeypatch, budget, value):
    await archive.async_record_month(SN, sample(), NOW)
    monkeypatch.setattr(archive_browse, budget, value)
    with pytest.raises(ArchiveError) as error:
        await page(archive)
    assert error.value.kind is ArchiveFailure.BUDGET
    assert not archive.write_paused


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
async def test_actor_rejects_invalid_page_size(archive, limit):
    with pytest.raises(ValueError):
        await page(archive, limit=limit)


async def test_month_metadata_budget_and_empty_valid_range(archive, monkeypatch):
    await archive.async_record_month(SN, sample(ids=()), NOW)
    assert not (await page(archive)).records
    monkeypatch.setattr(archive_browse, "MAX_TIMELINE_BYTES", 1)
    with pytest.raises(ArchiveError) as error:
        await page(archive)
    assert error.value.kind is ArchiveFailure.BUDGET
    with pytest.raises(ValueError):
        await archive.async_recorded_page(SN, NOW, START, NOW)
    with pytest.raises(ValueError):
        await archive.async_recorded_page(SN, NOW - timedelta(days=1831), NOW, NOW)


async def test_continuations_are_bounded_expire_on_ttl_clock_reversal_and_clear(archive):
    await archive.async_record_month(SN, sample(), NOW)
    cursor = (await page(archive)).next_cursor
    store = RecordedCursors()
    tokens = [store.put(cursor, NOW) for _ in range(9)]
    assert store.get(tokens[0], NOW) is None
    assert store.get(tokens[-1], NOW) == cursor
    assert store.get(tokens[-1], NOW + timedelta(seconds=900)) is None
    token = store.put(cursor, NOW)
    assert store.get(token, START) is None
    token = store.put(cursor, NOW)
    store.clear()
    assert store.get(token, NOW) is None


@pytest.fixture(autouse=True)
def recorded_clock(freezer):
    freezer.move_to(NOW)


@pytest.fixture
async def device(hass, entry, app_client, freezer):
    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=1))
    await entry.runtime_data.coordinator.statistics.async_record(
        SN, sample(), NOW + timedelta(seconds=1)
    )
    await hass.async_block_till_done()
    app_client.reset_mock()
    return next(
        row.id
        for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", SN) in row.identifiers
    )


def data(device, **kwargs):
    return {"device_id": device, "start_date": "2026-08-01", "end_date": "2026-10-08", **kwargs}


async def call(hass, device, *, context=None, **kwargs):
    return await hass.services.async_call(
        "ninebot",
        "get_recorded_trips",
        data(device, **kwargs),
        blocking=True,
        return_response=True,
        context=context,
    )


async def test_action_offline_pagination_no_cloud_no_large_state(hass, entry, app_client, device):
    co = entry.runtime_data.coordinator
    co._invalidate_auth()
    original = co.data[SN]
    assert hass.services.supports_response("ninebot", "get_recorded_trips") is SupportsResponse.ONLY
    first = await call(hass, device, limit=1)
    assert first["source_mode"] == "ride_archive"
    assert first["rides"][0]["precision"]["mileages"] == 2
    assert first["rides"][0]["received_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert first["coverage"]["missing_months"] == ["202608"]
    assert not first["coverage"]["all_observed_lists_complete"]
    assert first["coverage"]["upstream_history_complete"] == "unverified"
    last = await call(hass, device, cursor=first["next_cursor"])
    assert len(last["rides"]) == 2 and last["next_cursor"] is None
    assert all(not row["speed_samples"] and "track" not in row for row in last["rides"])
    assert co.data[SN] is original
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_action_scope_revision_expiry_range_and_unload(
    hass, entry, app_client, device, freezer
):
    first = await call(hass, device, limit=1)
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, device, cursor=first["next_cursor"], end_date="2026-10-07")
    assert error.value.translation_key == "history_cursor"
    await entry.runtime_data.coordinator.statistics.async_record(
        SN, sample(ids=("new",)), NOW + timedelta(seconds=2)
    )
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, device, cursor=first["next_cursor"])
    assert error.value.translation_key == "history_cursor"
    freezer.move_to(NOW + timedelta(seconds=901))
    with pytest.raises(HomeAssistantError):
        await call(hass, device, cursor=first["next_cursor"])
    for start, end in (
        ("2026-10-09", "2026-10-08"),
        ("2000-01-01", "2026-10-08"),
        ("2000-01-01", "2000-01-02"),
    ):
        with pytest.raises(HomeAssistantError) as error:
            await call(hass, device, start_date=start, end_date=end)
        assert error.value.translation_key == "recorded_range"
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(HomeAssistantError):
        await call(hass, device)


async def test_action_rejects_budget_unavailable_and_late_ownership(
    hass, entry, device, monkeypatch
):
    co = entry.runtime_data.coordinator
    archive = co.statistics.archive
    actual = archive.async_recorded_page
    for kind, key in (
        (ArchiveFailure.BUDGET, "recorded_range"),
        (ArchiveFailure.BUSY, "calendar_unavailable"),
        (ArchiveFailure.CURSOR, "history_cursor"),
    ):
        monkeypatch.setattr(
            archive, "async_recorded_page", AsyncMock(side_effect=ArchiveError(kind))
        )
        with pytest.raises(HomeAssistantError) as error:
            await call(hass, device)
        assert error.value.translation_key == key
    assert not archive.write_paused

    async def lose_owner(*args, **kwargs):
        result = await actual(*args, **kwargs)
        co._ownership[SN] = co._ownership.get(SN, 0) + 1
        return result

    monkeypatch.setattr(archive, "async_recorded_page", lose_owner)
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, device)
    assert error.value.translation_key == "query_unavailable"
    co.statistics.archive_ready = False
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, device)
    assert error.value.translation_key == "calendar_unavailable"


async def test_user_permissions_before_reads_and_detail_cloud(hass, entry, app_client, device):
    rows = er.async_entries_for_device(er.async_get(hass), device)
    calendar = next(row.entity_id for row in rows if row.domain == "calendar")
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    user.permissions.check_entity.return_value = False
    context = Context(user_id="synthetic-restricted-reader")
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        with pytest.raises(Unauthorized):
            await call(hass, device, context=context)
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                "ninebot",
                "get_trip_detail",
                {"device_id": device, "ride_id": "first"},
                blocking=True,
                return_response=True,
                context=context,
            )
        app_client.async_get_trip_detail.assert_not_awaited()
        user.permissions.check_entity.side_effect = lambda entity, perm: entity == calendar
        assert (await call(hass, device, context=context))["rides"]
        user.permissions.access_all_entities.return_value = True
        assert (await call(hass, device, context=context))["rides"]
        user.is_active = False
        with pytest.raises(Unauthorized):
            await call(hass, device, context=context)
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=None)):
        with pytest.raises(Unauthorized):
            await call(hass, device, context=context)


async def test_permissions_rechecked_after_read_and_gps_needs_tracker(hass, entry, device):
    from homeassistant.core import ServiceCall

    from custom_components.ninebot.services import async_check_history_access

    rows = er.async_entries_for_device(er.async_get(hass), device)
    calendar = next(row.entity_id for row in rows if row.domain == "calendar")
    tracker = next(row.entity_id for row in rows if row.domain == "device_tracker")
    user = SimpleNamespace(is_active=True, permissions=Mock())
    user.permissions.access_all_entities.return_value = False
    user.permissions.check_entity.side_effect = lambda entity, perm: entity == calendar
    context = Context(user_id="synthetic-restricted-reader")
    service = ServiceCall(
        hass, "ninebot", "get_trip_detail", {"device_id": device}, context=context
    )
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        with pytest.raises(Unauthorized):
            await async_check_history_access(hass, service, entry.entry_id, include_track=True)
        user.permissions.check_entity.side_effect = lambda entity, perm: (
            entity in (calendar, tracker)
        )
        await async_check_history_access(hass, service, entry.entry_id, include_track=True)
    with patch.object(hass.auth, "async_get_user", AsyncMock(side_effect=[user, None])):
        with pytest.raises(Unauthorized):
            await call(hass, device, context=context)


@pytest.mark.parametrize("value", ["2026-02-30", "2026-1-02", "２０２６-10-08", 20261008, True])
def test_date_schema_rejects_ambiguous_values(value):
    with pytest.raises(vol.Invalid):
        business_date(value)


def test_schema_no_cloud_or_track_switches():
    assert business_date("2026-10-08") == "2026-10-08"
    for extra in ({"include_track": True}, {"refresh": True}, {"limit": True}):
        with pytest.raises(vol.Invalid):
            RECORDED_SCHEMA(data("synthetic-device", **extra))
