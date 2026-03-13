"""Config flow for the Ninebot integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NinebotApiClient
from .const import (
    CONF_CHARGING_SCAN_INTERVAL,
    CONF_DEBUG,
    CONF_DEFAULT_SCAN_INTERVAL,
    CONF_LANG,
    CONF_SCAN_INTERVAL,
    CONF_UNLOCKED_SCAN_INTERVAL,
    DEFAULT_CHARGING_SCAN_INTERVAL,
    DEFAULT_DEBUG,
    DEFAULT_LANG,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNLOCKED_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .exceptions import NinebotAuthError, NinebotConnectionError, NinebotError


async def _async_validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate credentials by attempting to login to Ninebot cloud."""
    client = NinebotApiClient(
        session=async_get_clientsession(hass),
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        lang=data.get(CONF_LANG, DEFAULT_LANG),
        debug=bool(data.get(CONF_DEBUG, DEFAULT_DEBUG)),
    )
    await client.async_login()


def _safe_scan_interval(value: Any, default: int) -> int:
    """Return a safe scan interval value and avoid flow-time exceptions."""
    try:
        return max(MIN_SCAN_INTERVAL, int(value))
    except (TypeError, ValueError):
        return max(MIN_SCAN_INTERVAL, int(default))


class NinebotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ninebot."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            unique_id = username.lower()
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            normalized = {
                **user_input,
                CONF_USERNAME: username,
                CONF_PASSWORD: str(user_input.get(CONF_PASSWORD, "")),
                CONF_LANG: user_input.get(CONF_LANG, DEFAULT_LANG),
                CONF_DEFAULT_SCAN_INTERVAL: _safe_scan_interval(
                    user_input.get(CONF_DEFAULT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    DEFAULT_SCAN_INTERVAL,
                ),
                CONF_UNLOCKED_SCAN_INTERVAL: _safe_scan_interval(
                    user_input.get(CONF_UNLOCKED_SCAN_INTERVAL, DEFAULT_UNLOCKED_SCAN_INTERVAL),
                    DEFAULT_UNLOCKED_SCAN_INTERVAL,
                ),
                CONF_CHARGING_SCAN_INTERVAL: _safe_scan_interval(
                    user_input.get(CONF_CHARGING_SCAN_INTERVAL, DEFAULT_CHARGING_SCAN_INTERVAL),
                    DEFAULT_CHARGING_SCAN_INTERVAL,
                ),
                CONF_DEBUG: bool(user_input.get(CONF_DEBUG, DEFAULT_DEBUG)),
            }

            # Keep legacy key for backward compatibility.
            normalized[CONF_SCAN_INTERVAL] = normalized[CONF_DEFAULT_SCAN_INTERVAL]

            try:
                await _async_validate_input(self.hass, normalized)
            except NinebotAuthError:
                errors["base"] = "invalid_auth"
            except NinebotConnectionError:
                errors["base"] = "cannot_connect"
            except NinebotError:
                errors["base"] = "api_error"
            except Exception:  # pragma: no cover - defensive catch for flow UX
                errors["base"] = "unknown"

            if not errors:
                return self.async_create_entry(
                    title=f"Ninebot ({username})",
                    data=normalized,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_LANG, default=DEFAULT_LANG): vol.In(["zh", "zh-hant", "en"]),
                vol.Optional(CONF_DEFAULT_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL),
                ),
                vol.Optional(CONF_UNLOCKED_SCAN_INTERVAL, default=DEFAULT_UNLOCKED_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL),
                ),
                vol.Optional(CONF_CHARGING_SCAN_INTERVAL, default=DEFAULT_CHARGING_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL),
                ),
                vol.Optional(CONF_DEBUG, default=DEFAULT_DEBUG): bool,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return NinebotOptionsFlow(config_entry)


class NinebotOptionsFlow(config_entries.OptionsFlow):
    """Handle Ninebot options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            username = str(user_input.get(CONF_USERNAME, self.config_entry.data.get(CONF_USERNAME, ""))).strip()
            password = str(user_input.get(CONF_PASSWORD, self.config_entry.data.get(CONF_PASSWORD, "")))

            normalized = {
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_LANG: user_input.get(
                    CONF_LANG,
                    self.config_entry.options.get(
                        CONF_LANG,
                        self.config_entry.data.get(CONF_LANG, DEFAULT_LANG),
                    ),
                ),
                CONF_DEFAULT_SCAN_INTERVAL: _safe_scan_interval(
                    user_input.get(CONF_DEFAULT_SCAN_INTERVAL, self.config_entry.options.get(CONF_DEFAULT_SCAN_INTERVAL, self.config_entry.data.get(CONF_DEFAULT_SCAN_INTERVAL, self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))),
                    DEFAULT_SCAN_INTERVAL,
                ),
                CONF_UNLOCKED_SCAN_INTERVAL: _safe_scan_interval(
                    user_input.get(CONF_UNLOCKED_SCAN_INTERVAL, self.config_entry.options.get(CONF_UNLOCKED_SCAN_INTERVAL, self.config_entry.data.get(CONF_UNLOCKED_SCAN_INTERVAL, DEFAULT_UNLOCKED_SCAN_INTERVAL))),
                    DEFAULT_UNLOCKED_SCAN_INTERVAL,
                ),
                CONF_CHARGING_SCAN_INTERVAL: _safe_scan_interval(
                    user_input.get(CONF_CHARGING_SCAN_INTERVAL, self.config_entry.options.get(CONF_CHARGING_SCAN_INTERVAL, self.config_entry.data.get(CONF_CHARGING_SCAN_INTERVAL, DEFAULT_CHARGING_SCAN_INTERVAL))),
                    DEFAULT_CHARGING_SCAN_INTERVAL,
                ),
                CONF_DEBUG: bool(
                    user_input.get(
                        CONF_DEBUG,
                        self.config_entry.options.get(
                            CONF_DEBUG,
                            self.config_entry.data.get(CONF_DEBUG, DEFAULT_DEBUG),
                        ),
                    )
                ),
            }

            try:
                await _async_validate_input(self.hass, normalized)
            except NinebotAuthError:
                errors["base"] = "invalid_auth"
            except NinebotConnectionError:
                errors["base"] = "cannot_connect"
            except NinebotError:
                errors["base"] = "api_error"
            except Exception:  # pragma: no cover - defensive catch for flow UX
                errors["base"] = "unknown"

            if not errors:
                options = {
                    CONF_LANG: normalized[CONF_LANG],
                    CONF_DEFAULT_SCAN_INTERVAL: normalized[CONF_DEFAULT_SCAN_INTERVAL],
                    CONF_UNLOCKED_SCAN_INTERVAL: normalized[CONF_UNLOCKED_SCAN_INTERVAL],
                    CONF_CHARGING_SCAN_INTERVAL: normalized[CONF_CHARGING_SCAN_INTERVAL],
                    CONF_DEBUG: normalized[CONF_DEBUG],
                }

                data = dict(self.config_entry.data)
                data[CONF_USERNAME] = normalized[CONF_USERNAME]
                data[CONF_PASSWORD] = normalized[CONF_PASSWORD]
                data[CONF_LANG] = normalized[CONF_LANG]
                data[CONF_DEBUG] = normalized[CONF_DEBUG]
                data[CONF_SCAN_INTERVAL] = normalized[CONF_DEFAULT_SCAN_INTERVAL]
                data[CONF_DEFAULT_SCAN_INTERVAL] = normalized[CONF_DEFAULT_SCAN_INTERVAL]
                data[CONF_UNLOCKED_SCAN_INTERVAL] = normalized[CONF_UNLOCKED_SCAN_INTERVAL]
                data[CONF_CHARGING_SCAN_INTERVAL] = normalized[CONF_CHARGING_SCAN_INTERVAL]

                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=data,
                    options=options,
                )
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=self.config_entry.data.get(CONF_USERNAME, ""),
                ): str,
                vol.Required(
                    CONF_PASSWORD,
                    default=self.config_entry.data.get(CONF_PASSWORD, ""),
                ): str,
                vol.Optional(
                    CONF_LANG,
                    default=self.config_entry.options.get(
                        CONF_LANG,
                        self.config_entry.data.get(CONF_LANG, DEFAULT_LANG),
                    ),
                ): vol.In(["zh", "zh-hant", "en"]),
                vol.Optional(
                    CONF_DEFAULT_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_DEFAULT_SCAN_INTERVAL,
                        self.config_entry.data.get(CONF_DEFAULT_SCAN_INTERVAL, self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                vol.Optional(
                    CONF_UNLOCKED_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_UNLOCKED_SCAN_INTERVAL,
                        self.config_entry.data.get(CONF_UNLOCKED_SCAN_INTERVAL, DEFAULT_UNLOCKED_SCAN_INTERVAL),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                vol.Optional(
                    CONF_CHARGING_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_CHARGING_SCAN_INTERVAL,
                        self.config_entry.data.get(CONF_CHARGING_SCAN_INTERVAL, DEFAULT_CHARGING_SCAN_INTERVAL),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                vol.Optional(
                    CONF_DEBUG,
                    default=self.config_entry.options.get(
                        CONF_DEBUG,
                        self.config_entry.data.get(CONF_DEBUG, DEFAULT_DEBUG),
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
