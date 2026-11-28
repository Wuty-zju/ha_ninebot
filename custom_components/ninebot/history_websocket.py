"""Authorized bounded local invalidations, without GPS or cloud queries."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from homeassistant.auth import EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.components import websocket_api
from homeassistant.core import Context, Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .assistant_views import archive_vehicle_label
from .compat import validation as vol
from .const import DOMAIN
from .coordinator import NinebotCoordinator
from .ride_archive import ArchiveError
from .ride_comparison import async_archive_ride_row, async_open_ride_view, async_validate_ride_view
from .ride_receipt import async_open_ride_receipt
from .services import (
    assert_query_scope,
    async_check_history_access,
    query_month,
    resolve_vehicle,
    ride_identifier,
)

KEY = "ninebot_history_feeds"
MAX_CONNECTION = 8
MAX_TOTAL = 64
DEBOUNCE = 0.25
RETRY_DELAYS = (1, 5)
ACCESS_INTERVAL = 10
MAX_DISCOVERED = 100


@dataclass(frozen=True)
class Scope:
    device: str
    entry: str
    sn: str
    coordinator: NinebotCoordinator
    generation: int
    ownership: int


def history_call(hass: HomeAssistant, context: Context, device: str) -> ServiceCall:
    return ServiceCall(hass, DOMAIN, "get_recorded_trips", {"device_id": device}, context=context)


class HistoryFeed:
    """One bounded connection-owned subscription and at most one version read."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        message: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.connection = connection
        self.identity: int = message["id"]
        self.context: Context = connection.context(message)
        self.include_track: bool = message.get("include_track", False)
        self.view: str | None = message.get("view")
        self.scopes: list[Scope] = []
        self.listeners: list[Callable[[], None]] = []
        self.timer: asyncio.TimerHandle | None = None
        self.task: asyncio.Task[Any] | None = None
        self.closed = False
        self.ready = False
        self.dirty = False
        self.errors = 0
        self.access_timer: asyncio.TimerHandle | None = None
        self.access_task: asyncio.Task[Any] | None = None
        self.access_dirty = False

    @callback
    def cancel(self) -> None:
        """Core iterates subscriptions.values() on close: never mutate that dict here."""
        if self.closed:
            return
        self.closed = True
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
        self.task = None
        if self.access_timer:
            self.access_timer.cancel()
            self.access_timer = None
        if self.access_task and self.access_task is not asyncio.current_task():
            self.access_task.cancel()
        self.access_task = None
        for remove in self.listeners:
            remove()
        self.listeners.clear()
        self.scopes.clear()
        feeds(self.hass).discard(self)

    @callback
    def check_scope(self, scope: Scope) -> None:
        co = scope.coordinator
        if (
            co._generation != scope.generation
            or co._ownership.get(scope.sn, 0) != scope.ownership
            or not co.local_vehicle_available(scope.sn)
        ):
            self.hass.loop.call_soon(self.finish, "unavailable")

    def finish(self, reason: str) -> None:
        if not self.closed:
            if self.ready:
                self.connection.send_event(
                    self.identity, {"schema_version": 1, "type": "closed", "reason": reason}
                )
            else:
                self.connection.send_error(
                    self.identity, "unavailable", "Local history is unavailable"
                )
                self.connection.subscriptions.pop(self.identity, None)
            self.cancel()

    @callback
    def changed(self) -> None:
        if self.closed:
            return
        if not self.ready or self.task:
            self.dirty = True
        elif self.timer is None:
            self.timer = self.hass.loop.call_later(DEBOUNCE, self.launch)

    @callback
    def launch(self) -> None:
        self.timer = None
        if not self.closed:
            self.task = self.hass.async_create_task(self.send())

    async def versions(self) -> list[dict[str, str]]:
        result = []
        for scope in tuple(self.scopes):
            if self.closed:
                raise asyncio.CancelledError
            co = scope.coordinator
            await self.check_access(scope)
            version = await co.statistics.archive.async_catalog_version(scope.sn)
            await self.check_access(scope)
            result.append({"device_id": scope.device, "selection_version": version})
        await self.check_link()
        if self.closed:
            raise asyncio.CancelledError
        return result

    async def check_link(self) -> None:
        if self.view is not None:
            await async_validate_ride_view(
                self.hass, self.context, self.view, {scope.device for scope in self.scopes}
            )

    async def check_access(self, scope: Scope) -> None:
        await async_check_history_access(
            self.hass,
            history_call(self.hass, self.context, scope.device),
            scope.entry,
            include_track=self.include_track,
        )
        assert_query_scope(
            self.hass, scope.device, scope.entry, scope.sn, self.include_track, require_live=False
        )
        co = scope.coordinator
        if (
            not co.statistics.archive_ready
            or co._generation != scope.generation
            or co._ownership.get(scope.sn, 0) != scope.ownership
        ):
            raise HomeAssistantError

    @callback
    def watch_access(self) -> None:
        """Check only the current in-memory authorization, even on a quiet archive."""
        devices = {scope.device for scope in self.scopes}

        @callback
        def changed(event: Event[Any]) -> None:
            if (
                event.data.get("user_id") == self.context.user_id
                or event.data.get("device_id") in devices
                or event.data.get("entity_id", "").startswith(("calendar.", "device_tracker."))
            ):
                self.launch_access()

        listen = self.hass.bus.async_listen
        self.listeners.extend(
            (
                listen(EVENT_USER_UPDATED, changed),
                listen(EVENT_USER_REMOVED, changed),
                listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, changed),
                listen(er.EVENT_ENTITY_REGISTRY_UPDATED, changed),
            )
        )
        self.access_timer = self.hass.loop.call_later(ACCESS_INTERVAL, self.launch_access)

    @callback
    def launch_access(self) -> None:
        if self.access_timer:
            self.access_timer.cancel()
            self.access_timer = None
        if self.access_task:
            self.access_dirty = True
        elif not self.closed:
            # Cached auth can finish eagerly before the task is assigned here.
            self.access_task = self.hass.async_create_task(self.recheck_access(), eager_start=False)

    async def recheck_access(self) -> None:
        try:
            for scope in tuple(self.scopes):
                await self.check_access(scope)
            await self.check_link()
        except Unauthorized:
            self.finish("denied")
        except HomeAssistantError as err:
            reason = (
                "denied"
                if getattr(err, "translation_key", None) == "query_coordinates"
                else "unavailable"
            )
            self.finish(reason)
        finally:
            self.access_task = None
            if not self.closed:
                if self.access_dirty:
                    self.access_dirty = False
                    self.launch_access()
                else:
                    self.access_timer = self.hass.loop.call_later(
                        ACCESS_INTERVAL, self.launch_access
                    )

    async def send(self) -> None:
        self.dirty = False
        try:
            versions = await self.versions()
            self.errors = 0
            self.connection.send_event(
                self.identity, {"schema_version": 1, "type": "changed", "vehicles": versions}
            )
        except Unauthorized:
            self.finish("denied")
        except HomeAssistantError:
            self.finish("unavailable")
        except (ArchiveError, OSError, ValueError):
            self.errors += 1
            if self.errors >= 3:
                self.finish("unavailable")
            elif not self.closed:
                self.timer = self.hass.loop.call_later(RETRY_DELAYS[self.errors - 1], self.launch)
        finally:
            self.task = None
            if self.dirty and not self.closed:
                self.changed()


@callback
def feeds(hass: HomeAssistant) -> set[HistoryFeed]:
    return cast(set[HistoryFeed], hass.data.setdefault(KEY, set()))


@callback
def async_close_entry_feeds(hass: HomeAssistant, entry: str) -> None:
    for feed in tuple(feeds(hass)):
        if any(scope.entry == entry for scope in feed.scopes):
            feed.finish("unloaded")


@callback
def async_revoke_coordinate_access(hass: HomeAssistant, entry: str) -> None:
    for feed in tuple(feeds(hass)):
        if feed.include_track and any(scope.entry == entry for scope in feed.scopes):
            feed.finish("denied")


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ninebot/history/subscribe",
        vol.Required("device_ids"): vol.All(
            [vol.All(str, vol.Length(min=1, max=128))], vol.Length(min=1, max=5)
        ),
        vol.Optional("include_track", default=False): bool,
        vol.Optional("view"): vol.All(str, vol.Match(r"^[a-f0-9]{32}$")),
    }
)
@websocket_api.async_response
async def async_subscribe_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    ids: list[str] = message["device_ids"]
    pending = sum(
        isinstance(getattr(remove, "__self__", None), HistoryFeed)
        for remove in connection.subscriptions.values()
    )
    if len(set(ids)) != len(ids) or pending >= MAX_CONNECTION or len(feeds(hass)) >= MAX_TOTAL:
        connection.send_error(
            message["id"],
            "invalid_format",
            "Choose 1–5 distinct vehicles; subscription limit reached",
        )
        return
    feed = HistoryFeed(hass, connection, message)
    feeds(hass).add(feed)
    connection.subscriptions[message["id"]] = feed.cancel
    feed.task = asyncio.current_task()
    try:
        for device in ids:
            entry, sn = resolve_vehicle(hass, device, require_live=False)
            co = entry.runtime_data.coordinator
            feed.scopes.append(
                Scope(device, entry.entry_id, sn, co, co._generation, co._ownership.get(sn, 0))
            )
        for scope in feed.scopes:
            feed.listeners.append(
                scope.coordinator.statistics.async_add_archive_listener(scope.sn, feed.changed)
            )
            feed.listeners.append(
                scope.coordinator.async_add_listener(partial(feed.check_scope, scope))
            )
        versions = await feed.versions()
        if feed.closed:
            return
        # Changes during authorization/read are retained by ready=False/dirty.
        feed.ready = True
        if feed.include_track or feed.view:
            feed.watch_access()
        connection.send_result(message["id"])
        connection.send_event(
            message["id"], {"schema_version": 1, "type": "baseline", "vehicles": versions}
        )
    except Unauthorized:
        connection.send_error(message["id"], "unauthorized", "History access denied")
        feed.cancel()
        connection.subscriptions.pop(message["id"], None)
    except (HomeAssistantError, ArchiveError, OSError, ValueError):
        connection.send_error(message["id"], "unavailable", "Local history is unavailable")
        feed.cancel()
        connection.subscriptions.pop(message["id"], None)
    except asyncio.CancelledError:
        feed.cancel()
    finally:
        feed.task = None
        if feed.dirty and not feed.closed:
            feed.changed()


@websocket_api.websocket_command({vol.Required("type"): "ninebot/history/vehicles"})
@websocket_api.async_response
async def async_history_vehicles(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    """Discover loaded archive scopes the original HA user can replay."""
    context = connection.context(message)
    registry = dr.async_get(hass)
    rows: list[dict[str, str]] = []
    for account in hass.config_entries.async_entries(DOMAIN):
        for device in dr.async_entries_for_config_entry(registry, account.entry_id):
            try:
                entry, sn = resolve_vehicle(hass, device.id, require_live=False)
                if (
                    entry is not account
                    or not entry.runtime_data.coordinator.statistics.archive_ready
                ):
                    continue
                await async_check_history_access(
                    hass, history_call(hass, context, device.id), entry.entry_id, include_track=True
                )
                assert_query_scope(hass, device.id, entry.entry_id, sn, True, require_live=False)
            except HomeAssistantError:
                continue
            if len(rows) == MAX_DISCOVERED:
                connection.send_result(message["id"], {"vehicles": rows, "more_available": True})
                return
            rows.append(
                {
                    "account_id": entry.entry_id,
                    "device_id": device.id,
                    "label": archive_vehicle_label(
                        device.name_by_user or device.name, sn, entry.data
                    ),
                }
            )
    connection.send_result(message["id"], {"vehicles": rows, "more_available": False})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ninebot/history/ride_summary",
        vol.Required("account_id"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Required("device_id"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Required("ride_id"): ride_identifier,
        vol.Required("query_month"): query_month,
    }
)
@websocket_api.async_response
async def async_ride_summary(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    """Reopen a browser favorite with current archive permission and current local measurements."""
    device_id = message["device_id"]
    try:
        entry, sn = resolve_vehicle(hass, device_id, require_live=False)
        device = dr.async_get(hass).async_get(device_id)
        if device is None or entry.entry_id != message["account_id"]:
            raise Unauthorized
        co = entry.runtime_data.coordinator
        scope = Scope(device_id, entry.entry_id, sn, co, co._generation, co._ownership.get(sn, 0))
        call = history_call(hass, connection.context(message), device_id)

        def current() -> bool:
            return (
                co._generation == scope.generation
                and co._ownership.get(sn, 0) == scope.ownership
                and co.local_vehicle_available(sn)
            )

        async def check() -> None:
            await async_check_history_access(hass, call, scope.entry, include_track=True)
            assert_query_scope(hass, device_id, scope.entry, sn, True, require_live=False)
            if not current() or not co.statistics.archive_ready:
                raise HomeAssistantError

        await check()
        row = await async_archive_ride_row(
            co.statistics.archive,
            sn,
            message["query_month"],
            message["ride_id"],
            device_id=device_id,
            vehicle_label=archive_vehicle_label(device.name_by_user or device.name, sn, entry.data),
            guard=current,
        )
        await check()
    except Unauthorized:
        connection.send_error(message["id"], "unauthorized", "History access denied")
    except (HomeAssistantError, ArchiveError, OSError, ValueError):
        connection.send_error(message["id"], "unavailable", "The ride could not be opened")
    else:
        connection.send_result(message["id"], {"ride": row})


@callback
def async_register_history_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, async_subscribe_history)
    websocket_api.async_register_command(hass, async_history_vehicles)
    websocket_api.async_register_command(hass, async_ride_summary)
    websocket_api.async_register_command(hass, async_open_ride_view)
    websocket_api.async_register_command(hass, async_open_ride_receipt)
