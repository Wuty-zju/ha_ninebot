"""Authorized bounded local invalidations, without GPS or cloud queries."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .compat import validation as vol
from .coordinator import NinebotCoordinator
from .ride_archive import ArchiveError
from .services import assert_query_scope, async_check_history_access, resolve_vehicle

KEY = "ninebot_history_feeds"
MAX_CONNECTION = 8
MAX_TOTAL = 64
DEBOUNCE = 0.25
RETRY_DELAYS = (1, 5)


@dataclass(frozen=True)
class Scope:
    device: str
    entry: str
    sn: str
    coordinator: NinebotCoordinator
    generation: int
    ownership: int


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
        self.scopes: list[Scope] = []
        self.listeners: list[Callable[[], None]] = []
        self.timer: asyncio.TimerHandle | None = None
        self.task: asyncio.Task[Any] | None = None
        self.closed = False
        self.ready = False
        self.dirty = False
        self.errors = 0

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
            call = ServiceCall(
                self.hass,
                "ninebot",
                "get_recorded_trips",
                {"device_id": scope.device},
                context=self.context,
            )
            co = scope.coordinator
            await async_check_history_access(self.hass, call, scope.entry)
            version = await co.statistics.archive.async_catalog_version(scope.sn)
            await async_check_history_access(self.hass, call, scope.entry)
            assert_query_scope(
                self.hass, scope.device, scope.entry, scope.sn, False, require_live=False
            )
            if (
                not co.statistics.archive_ready
                or co._generation != scope.generation
                or co._ownership.get(scope.sn, 0) != scope.ownership
            ):
                raise HomeAssistantError
            result.append({"device_id": scope.device, "selection_version": version})
        if self.closed:
            raise asyncio.CancelledError
        return result

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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ninebot/history/subscribe",
        vol.Required("device_ids"): vol.All(
            [vol.All(str, vol.Length(min=1, max=128))], vol.Length(min=1, max=5)
        ),
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


@callback
def async_register_history_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, async_subscribe_history)
