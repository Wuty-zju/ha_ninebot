"""Optional packaged dashboard assets; no automatic dashboard changes."""

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.importlib import async_import_module

CARD_URL = "/ninebot-static/ninebot-trip-card.js"
ARCHIVE_PATH = "ninebot-archive"
ARCHIVE_URL = "/ninebot-static/archive-replay-panel.js"


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


async def async_register_archive_panel(hass: HomeAssistant) -> None:
    """Use HA's authenticated frontend after the first account loads."""
    if "frontend" not in hass.config.components:
        return
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
