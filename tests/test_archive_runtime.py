"""The real archive consumers and HA startup, entirely offline and isolated."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_runtime import ArchiveStatistics, archive_path
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.period_statistics import day_summary
from custom_components.ninebot.raw import Endpoint
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure
from custom_components.ninebot.statistics_actions import statistics_response
from custom_components.ninebot.statistics_store import TravelStatisticsStore
from custom_components.ninebot.travel import parse_ride

NOW = datetime(2026, 10, 8, 4, tzinfo=UTC)
SN = "SyntheticSN"
PROFILE = VehicleProfile(SN, "Synthetic rider", "Model")
REAL_WRITE = Store._async_write_data


@pytest.fixture(autouse=True)
def isolated_archive_directory(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


def many_rides(count=700, month="202609", *, prefix="r"):
    start = datetime(2026, 9, 20, 4, tzinfo=UTC)
    return travel(
        {
            "times": count,
            "total_mileages": count,
            "ec": count * 50,
            "duration": count * 60,
            "list": [
                {
                    "travel_id": f"{prefix}-{index}",
                    "id": f"detail-{prefix}-{index}",
                    "mileages": 1,
                    "ec": 50,
                    "duration": 60,
                    "speed": 30,
                    "start_time": int(start.timestamp()) + index * 60,
                    "end_time": int(start.timestamp()) + (index + 1) * 60,
                }
                for index in range(count)
            ],
        },
        month,
    )


async def seed(hass, entry, *, count=2):
    store = ArchiveStatistics(hass, entry.entry_id, entry.data["business_uid"])
    await store.async_load()
    await store.async_record_profiles((PROFILE,), NOW, True, lambda: True)
    await store.async_record(SN, many_rides(count), NOW, "0.1.7")
    await store.async_close()


async def test_consumed_archive_keeps_large_months_accounts_and_freezes_legacy(hass, freezer):
    freezer.move_to(NOW)
    legacy = TravelStatisticsStore(hass, "large-entry")
    legacy.update(SN, many_rides(2), NOW - timedelta(days=1))
    with patch.object(Store, "_async_write_data", REAL_WRITE):
        await legacy.async_save()
    original_path = Path(legacy._store.path)
    original = await hass.async_add_executor_job(original_path.read_bytes)
    store = ArchiveStatistics(hass, "large-entry", "owner-one")
    try:
        await store.async_load()
        assert store.restored and store.archive_ready and not store.persistence_enabled
        await store.async_record_profiles(
            (PROFILE, VehicleProfile("another", "Other", "Model")), NOW, True, lambda: True
        )
        await store.async_record(SN, many_rides(), NOW)
        await store.async_record("another", many_rides(prefix="other"), NOW)
        first = await store.async_view(SN, ("202608", "202609"))
        second = await store.async_view("another", ("202609",))
        rides, complete = first.month_rides(SN, "202609")
        assert complete and len(rides) == 700
        assert len(second.month_rides("another", "202609")[0]) == 700
        assert not set(r.ride_id for r in rides) & set(
            r.ride_id for r in second.month_rides("another", "202609")[0]
        )
        response = statistics_response(first, SN, ("202609",), NOW, True)
        day = next(row for row in response["days"] if row["date"] == "2026-09-20")
        assert (day["ride_count"], day["duration_s"], day["distance_km"], day["energy_wh"]) == (
            700,
            42000,
            700,
            35000,
        )
        assert response["source_mode"] == "ride_archive"
        assert len(store.month_rides(SN, "202609")[0]) == 700
        await store.async_save()
        assert await hass.async_add_executor_job(original_path.read_bytes) == original
    finally:
        await store.async_close()
    reopened = ArchiveStatistics(hass, "large-entry", "owner-one")
    try:
        await reopened.async_load()
        assert len((await reopened.async_view(SN, ("202609",))).month_rides(SN, "202609")[0]) == 700
        assert await hass.async_add_executor_job(original_path.read_bytes) == original
    finally:
        await reopened.async_close()


async def test_same_identity_in_two_months_and_sparse_valid_facts(hass, freezer):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "entry", "owner")
    try:
        await store.async_load()
        full = many_rides(1)
        await store.async_record(SN, full, NOW)
        sparse = travel({"times": 1, "list": [{"travel_id": "r-0"}]}, "202610")
        await store.async_record(SN, sparse, NOW + timedelta(seconds=1))
        view = await store.async_view(SN, ("202609", "202610"))
        for month in ("202609", "202610"):
            rows, complete = view.month_rides(SN, month)
            assert complete and rows[0].source_month == month
            assert rows[0].distance_m == 1000 and "distance_m" in rows[0].current_fields
        day = day_summary(view, SN, datetime(2026, 9, 20).date(), NOW + timedelta(seconds=1))
        assert day.ride_count == 1 and day.distance_km == 1
    finally:
        await store.async_close()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("cloud_error", [NinebotError(ErrorKind.CONNECTION), NinebotAuthError()])
async def test_offline_cold_start_reads_archive_without_live_permission(
    hass, entry, app_client, freezer, cloud_error
):
    freezer.move_to(NOW)
    await seed(hass, entry)
    app_client.async_list_vehicles.side_effect = cloud_error
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        co = entry.runtime_data.coordinator
        assert entry.state is ConfigEntryState.LOADED
        assert not co.last_update_success and not co._authenticated
        flows = hass.config_entries.flow.async_progress_by_handler("ninebot")
        assert bool(flows) is isinstance(cloud_error, NinebotAuthError)
        assert co.local_vehicle_available(SN) and not co.fresh(SN, "profile")
        assert not co.controls_enabled(SN, "engine/start")
        app_client.async_get_status.assert_not_awaited()
        app_client.async_control.assert_not_awaited()
        device = next(
            device
            for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if ("ninebot", SN) in device.identifiers
        )
        result = await hass.services.async_call(
            "ninebot",
            "get_statistics",
            {"device_id": device.id, "start_month": "202609", "end_month": "202609"},
            blocking=True,
            return_response=True,
        )
        assert result["summary"]["ride_count"] == 2
        assert result["source_mode"] == "ride_archive"
        for action, extra in (
            ("get_trips", {"month": "202609"}),
            ("get_history", {"start_month": "202609", "end_month": "202609"}),
        ):
            history = await hass.services.async_call(
                "ninebot",
                action,
                {"device_id": device.id, **extra},
                blocking=True,
                return_response=True,
            )
            assert len(history["rides"]) == 2
        app_client.async_get_travel.assert_not_awaited()
        app_client.async_get_trip_detail.assert_not_awaited()
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_cached_profile_never_bypasses_local_session_owner(hass, entry, app_client, freezer):
    freezer.move_to(NOW)
    await seed(hass, entry)
    with patch("custom_components.ninebot.session_uid", return_value="wrong-owner"):
        with pytest.raises(ConfigEntryAuthFailed):
            from custom_components.ninebot import async_setup_entry

            await async_setup_entry(hass, entry)
    app_client.async_list_vehicles.assert_not_awaited()
    assert archive_path(hass, entry.entry_id).is_file()


async def test_bad_archive_keeps_original_and_never_overwrites_legacy(hass):
    path = archive_path(hass, "bad")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"invalid-sqlite-original")
    store = ArchiveStatistics(hass, "bad", "owner")
    try:
        await store.async_load()
        assert not store.archive_ready and store.error is not None
        await store.async_record(SN, many_rides(1), NOW)
        await store.async_save()
        assert path.read_bytes() == b"invalid-sqlite-original"
        assert not await hass.async_add_executor_job(Path(store._store.path).exists)
    finally:
        await store.async_close()


async def test_same_clock_ordered_receipts_correct_disk_facts_and_ignore_lost_owner(hass, freezer):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "receipt-entry", "owner")
    try:
        await store.async_load()
        first = many_rides(1)
        await store.async_record(SN, first, NOW)
        changed = replace(
            first,
            rides=(replace(first.rides[0], distance_m=2000),),
        )
        await store.async_record(SN, changed, NOW, guard=lambda: True, receipt_ordered=True)
        archived = await store.async_cached_month(SN, "202609")
        assert archived.travel.rides[0].distance_m == 2000
        await store.async_record(SN, first, NOW, guard=lambda: False, receipt_ordered=True)
        assert (await store.async_cached_month(SN, "202609")).travel.rides[0].distance_m == 2000
        await store.async_record_profiles((PROFILE,), NOW, True, lambda: True)
        renamed = replace(PROFILE, name="Renamed")
        await store.async_record_profiles((renamed,), NOW, True, lambda: True)
        assert (await store.async_cached_profiles())[0].name == "Renamed"
    finally:
        await store.async_close()


async def test_archive_failure_recovery_views_detail_and_guards(hass, freezer):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "fault-entry", "owner")
    try:
        assert (await store.async_view(SN, ("202609",))).month(SN, "202609") is None
        assert await store.async_cached_month(SN, "202609") is None
        assert await store.async_cached_profiles() == ()
        await store.async_record_profiles((PROFILE,), NOW, True, lambda: True)
        detail = parse_ride(
            {"travel_id": "r-0", "ec": 99},
            "202609",
            source=Endpoint.TRIP_DETAIL,
        )
        await store.async_record_detail(SN, detail, NOW, lambda: True)
        await store.async_load()
        await store.async_record_profiles((PROFILE,), NOW, False, lambda: True)
        await store.async_record_profiles((PROFILE,), NOW, True, lambda: False)
        baseline = travel({"times": 1, "list": [{"travel_id": "r-0", "ec": 50}]}, "202609")
        await store.async_record(SN, baseline, NOW)
        await store.async_record_detail(SN, detail, NOW + timedelta(seconds=1), lambda: False)
        await store.async_record_detail(SN, detail, NOW + timedelta(seconds=1), lambda: True)
        assert (await store.async_cached_month(SN, "202609")).travel.rides[0].energy_raw == 99
        for method, invoke in (
            ("async_record_month", lambda: store.async_record(SN, many_rides(1), NOW)),
            (
                "async_record_profiles",
                lambda: store.async_record_profiles((PROFILE,), NOW, True, lambda: True),
            ),
            (
                "async_record_detail",
                lambda: store.async_record_detail(SN, detail, NOW, lambda: True),
            ),
            ("async_profiles", store.async_cached_profiles),
            ("async_months", store.async_refresh_projection),
        ):
            with patch.object(
                store.archive, method, side_effect=ArchiveError(ArchiveFailure.STORAGE)
            ):
                await invoke()
            assert store.error is ArchiveFailure.STORAGE
        await store.async_record_profiles((PROFILE,), NOW, True, lambda: True)
        assert store.error is None and store.writable
        old = store._projection
        await store._project(lambda: False)
        assert store._projection is old
        await store.archive.async_close()
        assert await store.async_cached_profiles() == ()
    finally:
        await store.async_close()
