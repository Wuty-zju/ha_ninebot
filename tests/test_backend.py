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
