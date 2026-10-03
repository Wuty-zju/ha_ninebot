"""Backend contract independent of HA representations and protocol encryption."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .capabilities import CONTROL_ACTIONS
from .client import NinecliClient
from .raw import BACKEND_VERSION, Endpoint


@dataclass(frozen=True)
class BackendResult:
    payload: Any
    endpoint: Endpoint
    received_at: datetime
    query_month: str | None = None
    backend_version: str = BACKEND_VERSION
    endpoint_version: str | None = None


class NinebotBackend(Protocol):
    """Transport support is not proof of vehicle ownership or permissions."""

    endpoints: frozenset[Endpoint]
    control_actions: frozenset[str]

    async def async_vehicles(self) -> BackendResult: ...
    async def async_status(self, vehicle: str) -> BackendResult: ...
    async def async_battery(self, vehicle: str) -> BackendResult: ...
    async def async_travel_month(self, vehicle: str, month: str) -> BackendResult: ...
    async def async_trip_detail(self, vehicle: str, detail_id: str) -> BackendResult: ...
    async def async_control(self, vehicle: str, action: str) -> None: ...
    async def async_close(self) -> None: ...


class NinecliBackend:
    """Thin adapter; the existing client/session transaction owns authentication."""

    endpoints = frozenset(Endpoint)
    control_actions = CONTROL_ACTIONS

    def __init__(self, client: NinecliClient) -> None:
        self.client = client

    async def async_vehicles(self) -> BackendResult:
        payload = await self.client.async_list_vehicles()
        return BackendResult(payload, Endpoint.VEHICLES, datetime.now(UTC))

    async def async_status(self, vehicle: str) -> BackendResult:
        payload = await self.client.async_get_status(vehicle)
        return BackendResult(payload, Endpoint.STATUS, datetime.now(UTC))

    async def async_battery(self, vehicle: str) -> BackendResult:
        payload = await self.client.async_get_battery(vehicle)
        return BackendResult(payload, Endpoint.BATTERY, datetime.now(UTC))

    async def async_travel_month(self, vehicle: str, month: str) -> BackendResult:
        payload = await self.client.async_get_travel(vehicle, month)
        return BackendResult(payload, Endpoint.TRAVEL, datetime.now(UTC), month)

    async def async_trip_detail(self, vehicle: str, detail_id: str) -> BackendResult:
        payload = await self.client.async_get_trip_detail(vehicle, detail_id)
        return BackendResult(payload, Endpoint.TRIP_DETAIL, datetime.now(UTC))

    async def async_control(self, vehicle: str, action: str) -> None:
        await self.client.async_control(vehicle, action)

    async def async_close(self) -> None:
        await self.client.async_close()
