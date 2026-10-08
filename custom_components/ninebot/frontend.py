"""Optional packaged dashboard assets; no automatic dashboard changes."""

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

CARD_URL = "/ninebot-static/ninebot-trip-card.js"


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
