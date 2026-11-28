"""Local ride receipt links resolve against the current authorized archive."""

import base64
import hashlib
import re
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr

from .assistant_views import archive_vehicle_label
from .compat import validation as vol
from .const import DOMAIN
from .ride_archive import ArchiveError
from .ride_comparison import async_archive_ride_row
from .services import assert_query_scope, async_check_history_access, resolve_vehicle
from .travel import opaque_id

EVENT_RIDE_RECEIPT = "ninebot_ride_receipt"
LOCATOR = re.compile(r"r1\.([0-9a-f]{32})\.([A-Za-z0-9_-]{39,1403})\Z")
NONCE_BYTES = 12


def receipt_tag(device_id: str, owner: str, ride_id: str) -> str:
    """Stable across revisions, restarts and notifications on multiple HA servers."""
    return "ninebot-ride-" + hashlib.sha256(f"{owner}.{device_id}.{ride_id}".encode()).hexdigest()


def receipt_locator(key: bytes, owner: str, device_id: str, ride_id: str) -> str:
    """Expose only the HA registry ID and an account-bound encrypted ride ID."""
    prefix = f"r1.{device_id}"
    nonce = secrets.token_bytes(NONCE_BYTES)
    payload = AESGCM(key).encrypt(nonce, ride_id.encode(), f"{owner}.{prefix}".encode())
    encoded = base64.urlsafe_b64encode(nonce + payload).decode().rstrip("=")
    return f"{prefix}.{encoded}"


def _ride_id(key: bytes, owner: str, device_id: str, encoded: str) -> str | None:
    try:
        payload = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        if base64.urlsafe_b64encode(payload).decode().rstrip("=") != encoded:
            return None
        nonce, ciphertext = payload[:NONCE_BYTES], payload[NONCE_BYTES:]
        ride_id = (
            AESGCM(key).decrypt(nonce, ciphertext, f"{owner}.r1.{device_id}".encode()).decode()
        )
    except (InvalidTag, ValueError):
        return None
    return ride_id if opaque_id(ride_id) == ride_id else None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ninebot/open_ride_receipt",
        vol.Required("receipt"): vol.All(str, vol.Length(min=75, max=1439)),
    }
)
@websocket_api.async_response
async def async_open_ride_receipt(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    match = LOCATOR.fullmatch(message["receipt"])
    if match is None:
        connection.send_error(message["id"], "invalid_format", "Invalid ride receipt")
        return
    device_id, encoded = match.groups()
    context = connection.context(message)
    call = ServiceCall(
        hass, DOMAIN, "get_recorded_trips", {"device_id": device_id}, context=context
    )
    try:
        entry, sn = resolve_vehicle(hass, device_id, require_live=False)
        runtime = entry.runtime_data
        co = runtime.coordinator
        generation, ownership = co._generation, co._ownership.get(sn, 0)
        statistics = co.statistics

        def scope_current() -> bool:
            assert_query_scope(hass, device_id, entry.entry_id, sn, True, require_live=False)
            return (
                entry.runtime_data is runtime
                and statistics.archive_ready
                and generation == co._generation
                and ownership == co._ownership.get(sn, 0)
            )

        async def check_access() -> None:
            if context.user_id is None:
                raise Unauthorized(context=context)
            await async_check_history_access(hass, call, entry.entry_id, include_track=True)
            if not scope_current():
                raise HomeAssistantError

        await check_access()
        # Keep this command usable when the Event entity is disabled after delivery.
        if runtime.events is None:
            raise HomeAssistantError
        key = await runtime.events.async_receipt_key()
        await check_access()
        owner = statistics.archive.owner
        ride_id = _ride_id(key, owner, device_id, encoded) if key else None
        device = dr.async_get(hass).async_get(device_id)
        if ride_id is None or device is None:
            raise HomeAssistantError
        row = await async_archive_ride_row(
            statistics.archive,
            sn,
            None,
            ride_id,
            device_id=device_id,
            vehicle_label=archive_vehicle_label(device.name_by_user or device.name, sn, entry.data),
            guard=scope_current,
        )
        await check_access()
        if row is None:
            raise HomeAssistantError
    except Unauthorized:
        connection.send_error(message["id"], "unauthorized", "Ride replay access denied")
    except (HomeAssistantError, ArchiveError, OSError, ValueError):
        connection.send_error(message["id"], "unavailable", "This ride receipt is unavailable")
    else:
        connection.send_result(message["id"], {"rides": [row]})
