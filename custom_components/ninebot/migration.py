"""Offline, conservative migration of two unrelated v1 layouts."""

import json
import re
import uuid
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .account import ACCOUNT_METADATA, AUTOMATIC_TITLE, AccountDisplay, account_update
from .adapters import number, text
from .const import (
    CONF_ACCOUNT,
    CONF_BUSINESS_UID,
    CONF_COORDINATES,
    CONF_IDENTITY_SCHEME,
    CONF_POLL_INTERVAL,
    CONF_SESSION_KEY,
    DEFAULT_COORDINATES,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .exceptions import NinebotError
from .session import SessionManager
from .storage import ModelStorage


def legacy_parameters(path: Path) -> dict[str, dict[str, float]]:
    """Whitelist only local configuration. No credentials, raw state or totals."""
    try:
        envelope = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    raw = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(raw, dict):
        return {}
    rows = raw.get("devices", raw)
    if not isinstance(rows, dict):
        return {}
    result = {}
    for sn, row in rows.items():
        if not isinstance(sn, str) or not isinstance(row, dict):
            continue
        values = {}
        for key, source, low, high in [
            ("voltage", "main_battery_voltage", 1, 300),
            ("capacity", "battery_capacity", 1, 500),
        ]:
            value = number(row.get(source), low, high)
            if value is not None:
                values[key] = value
        result[sn] = values
    return result


async def async_migrate(hass: HomeAssistant, entry: ConfigEntry, manager: SessionManager) -> bool:
    if entry.version > 2:
        return False
    if entry.version == 2:
        if entry.minor_version < 2:
            metadata, automatic, title = account_update(
                dict(entry.data),
                str(entry.data.get(CONF_ACCOUNT, "")),
                AccountDisplay(),
                entry.title,
            )
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, ACCOUNT_METADATA: metadata, AUTOMATIC_TITLE: automatic},
                options={CONF_COORDINATES: DEFAULT_COORDINATES, **entry.options},
                title=title,
                minor_version=2,
            )
        return True
    uid = text(entry.data.get(CONF_BUSINESS_UID))
    if uid and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", uid) is None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"migration_identity_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="migration_identity_invalid",
        )
        return False
    scheme = "app_v1" if uid else "open_v1"
    data: dict[str, Any] = {
        CONF_ACCOUNT: text(entry.data.get("username")) or "",
        CONF_IDENTITY_SCHEME: scheme,
        # Repeating an interrupted v1 migration must not leave a new orphan
        # directory on each attempt. This is an identifier, not a secret.
        CONF_SESSION_KEY: uuid.uuid5(
            uuid.NAMESPACE_URL, f"{DOMAIN}:{entry.entry_id}:v2-session"
        ).hex,
    }
    if uid:
        data[CONF_BUSINESS_UID] = uid
        source = Path(hass.config.path(".storage", "ninebot", uid))
        candidate = None
        try:
            _, candidate = await manager.async_import(source, uid)
            key = data[CONF_SESSION_KEY]
            async with manager.transaction(key):
                await manager.async_commit(candidate, key)
                await manager.async_finalize(key)
        except (NinebotError, OSError):
            # No cloud login in migration; setup will offer reauth.
            pass
        finally:
            if candidate is not None:
                await manager.async_discard(candidate)
    else:
        parameters = await hass.async_add_executor_job(
            legacy_parameters,
            Path(hass.config.path(".storage", f"ninebot_{entry.entry_id}_runtime")),
        )
        store = ModelStorage(hass, entry.entry_id)
        await store.async_load()
        if store.writable:
            for sn, values in parameters.items():
                model = store.model(sn)
                store.configure(
                    sn, {key: value for key, value in values.items() if getattr(model, key) is None}
                )
        await store.async_save()
    old_interval = entry.options.get(
        CONF_POLL_INTERVAL, entry.data.get("default_scan_interval", DEFAULT_POLL_INTERVAL)
    )
    parsed = number(old_interval, 30, 3600)
    options = {
        CONF_POLL_INTERVAL: int(parsed) if parsed is not None else DEFAULT_POLL_INTERVAL,
        CONF_COORDINATES: entry.options.get(CONF_COORDINATES, DEFAULT_COORDINATES),
    }
    metadata, automatic, title = account_update(
        dict(entry.data), data[CONF_ACCOUNT], AccountDisplay(), entry.title
    )
    data.update({ACCOUNT_METADATA: metadata, AUTOMATIC_TITLE: automatic})
    # Passwords are intentionally not carried into v2 ConfigEntry data.
    hass.config_entries.async_update_entry(
        entry, data=data, options=options, title=title, version=2, minor_version=2
    )
    ir.async_delete_issue(hass, DOMAIN, f"migration_identity_{entry.entry_id}")
    return True
