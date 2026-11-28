"""Optional packaged dashboard assets; no automatic dashboard changes."""

import logging
from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.importlib import async_import_module

CARD_URL = "/ninebot-static/ninebot-trip-card.js"
ARCHIVE_PATH = "ninebot-archive"
ARCHIVE_URL = "/ninebot-static/archive-replay-panel.js"
ARCHIVE_PANEL = "ninebot_archive_panel"
_LOGGER = logging.getLogger(__name__)


@dataclass
class _ArchivePanel:
    accounts: set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)
    unregister: Callable[[], None] | None = None


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register one fixed asset asynchronously, never an archive or config path."""
    # The manifest's HTTP dependency guarantees this during real component setup.
    # Direct test/setup calls without an HTTP component need no static route.
    if not (http := getattr(hass, "http", None)) or hass.data.get("ninebot_frontend"):
        return
    await http.async_register_static_paths(
        [StaticPathConfig("/ninebot-static", str(Path(__file__).parent / "frontend"), False)]
    )
    hass.data["ninebot_frontend"] = True


async def async_update_archive_panel(hass: HomeAssistant, entry_id: str, *, loaded: bool) -> None:
    """Share the authenticated panel until the last loaded account unloads."""
    state: _ArchivePanel | None = hass.data.get(ARCHIVE_PANEL)
    if state is None:
        state = hass.data[ARCHIVE_PANEL] = _ArchivePanel()
    async with state.lock:
        if loaded:
            state.accounts.add(entry_id)
            if state.unregister is None:
                try:
                    state.unregister = await _async_register_archive_panel(hass)
                except (HomeAssistantError, ImportError, ValueError):
                    _LOGGER.warning("The Ninebot Ride Archive panel is unavailable")
        else:
            state.accounts.discard(entry_id)
            if not state.accounts and state.unregister is not None:
                state.unregister()
                state.unregister = None


async def _async_register_archive_panel(hass: HomeAssistant) -> Callable[[], None] | None:
    # The manifest orders setup after frontend when that component is enabled.
    if "frontend" not in hass.config.components:
        return None
    frontend = await async_import_module(hass, "homeassistant.components.frontend")
    panel = await async_import_module(hass, "homeassistant.components.panel_custom")
    if not frontend.async_panel_exists(hass, ARCHIVE_PATH):
        await panel.async_register_panel(
            hass,
            frontend_url_path=ARCHIVE_PATH,
            webcomponent_name="ninebot-archive-panel",
            sidebar_title="Ninebot Ride Archive",
            sidebar_icon="mdi:map-clock-outline",
            module_url=ARCHIVE_URL,
            require_admin=False,
        )
        return lambda: frontend.async_remove_panel(hass, ARCHIVE_PATH, warn_if_unknown=False)
    return None
