import asyncio
from datetime import UTC
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.ninebot.backend import NinecliBackend
from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.exceptions import NinebotAuthError
from custom_components.ninebot.raw import Endpoint


async def test_backend_metadata_routes_and_no_extra_authentication():
    client = AsyncMock(spec=NinecliClient)
    client.vehicle_discovery_complete = True
    backend = NinecliBackend(client)
    assert backend.endpoints == frozenset(Endpoint)
    results = [
        await backend.async_vehicles(),
        await backend.async_status("vehicle"),
        await backend.async_battery("vehicle"),
        await backend.async_travel_month("vehicle", "202609"),
        await backend.async_trip_detail("vehicle", "ride"),
    ]
    assert [result.endpoint for result in results] == list(Endpoint)
    assert all(
        result.received_at.tzinfo is UTC
        and result.backend_version == "0.1.7"
        and result.endpoint_version is None
        for result in results
    )
    assert results[3].query_month == "202609"
    client.async_get_trip_detail.assert_awaited_once_with("vehicle", "ride")
    client.async_login.assert_not_awaited()
    await backend.async_control("vehicle", "bell")
    client.async_control.assert_awaited_once_with("vehicle", "bell")
    await backend.async_close()
    client.async_close.assert_awaited_once()
    client.async_get_status.side_effect = NinebotAuthError()
    with pytest.raises(NinebotAuthError):
        await backend.async_status("vehicle")


async def test_trip_detail_route_encodes_opaque_parameters(tmp_path):
    client = NinecliClient(tmp_path, AsyncMock())
    client._request = AsyncMock(return_value={"duration": 1})
    await client.async_get_trip_detail("vehicle/other?", "opaque/id?#")
    client._request.assert_awaited_once_with(
        "GET", "/vehicles/vehicle%2Fother%3F/travel/opaque%2Fid%3F%23"
    )


async def test_backend_measures_installed_version_once_and_unknown_stays_unknown():
    from importlib.metadata import PackageNotFoundError

    client = AsyncMock(spec=NinecliClient)
    with patch("custom_components.ninebot.backend.version", return_value="0.1.8") as measure:
        backend = NinecliBackend(client)
        assert (await backend.async_status("vehicle")).backend_version == "0.1.8"
        assert (await backend.async_battery("vehicle")).backend_version == "0.1.8"
        measure.assert_called_once_with("ninecli")
    with patch("custom_components.ninebot.backend.version", side_effect=PackageNotFoundError):
        assert (await NinecliBackend(client).async_status("vehicle")).backend_version is None


@pytest.mark.parametrize("first_complete", [False, True])
async def test_discovery_completeness_stays_with_its_payload_during_metadata_lookup(first_complete):
    client = AsyncMock(spec=NinecliClient)
    adapter = NinecliBackend(client)
    metadata_started, second_discovered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def discover():
        nonlocal calls
        calls += 1
        client.vehicle_discovery_complete = first_complete if calls == 1 else not first_complete
        if calls == 2:
            second_discovered.set()
        return [{"wnumber": f"synthetic-{calls}"}]

    async def metadata():
        metadata_started.set()
        await release.wait()
        return "0.1.7"

    client.async_list_vehicles.side_effect = discover
    adapter._version = AsyncMock(side_effect=metadata)
    first = asyncio.create_task(adapter.async_vehicles())
    await asyncio.wait_for(metadata_started.wait(), 5)
    second = asyncio.create_task(adapter.async_vehicles())
    await asyncio.wait_for(second_discovered.wait(), 5)
    release.set()
    earlier, later = await asyncio.gather(first, second)
    assert earlier.payload == [{"wnumber": "synthetic-1"}]
    assert earlier.vehicles_complete is first_complete
    assert later.payload == [{"wnumber": "synthetic-2"}]
    assert later.vehicles_complete is (not first_complete)
    client.async_login.assert_not_awaited()
