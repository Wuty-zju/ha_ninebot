"""Optional administrator review panel, using HA's existing frontend and connection."""

from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.importlib import async_import_module

from .frontend import async_register_frontend

PANEL_PATH = "ninebot-assistant"
PANEL_URL = "/ninebot-static/assistant-panel.js"


async def async_register(hass: HomeAssistant) -> Callable[[], None] | None:
    """Headless installations keep the authenticated API without loading a frontend."""
    if "frontend" not in hass.config.components:
        return None
    frontend = await async_import_module(hass, "homeassistant.components.frontend")
    panel = await async_import_module(hass, "homeassistant.components.panel_custom")
    if frontend.async_panel_exists(hass, PANEL_PATH):
        raise ValueError("Review panel path is occupied")
    await async_register_frontend(hass)
    await panel.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name="ninebot-assistant-panel",
        sidebar_title="Ninebot Assistant",
        sidebar_icon="mdi:clipboard-check-outline",
        module_url=PANEL_URL,
        embed_iframe=False,
        require_admin=True,
    )
    return lambda: frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
