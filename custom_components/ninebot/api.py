"""Async Ninebot cloud API client."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DEFAULT_TIMEOUT_SECONDS,
    DEVICE_BASE_URL,
    DEVICE_DYNAMIC_INFO_PATH,
    DEVICES_PATH,
    DOMAIN,
    LOGIN_BASE_URL,
    LOGIN_PATH,
)
from .exceptions import NinebotApiError, NinebotAuthError, NinebotConnectionError

_LOGGER = logging.getLogger(__name__)
_CACHE_VERSION = 1


class NinebotApiClient:
    """Ninebot API client wrapper used by coordinator and config flow."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        entry_id: str | None,
        session: ClientSession,
        username: str,
        password: str,
        lang: str,
        debug: bool = False,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._lang = lang
        self._debug = debug

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: int | None = None
        self._token_checked_at: int | None = None

        self._devices_cache: list[dict[str, Any]] = []
        self._devices_last_refresh: int | None = None
        self._store: Store[dict[str, Any]] | None = None
        self._cache_loaded = False
        if hass is not None and entry_id:
            self._store = Store(
                hass,
                _CACHE_VERSION,
                f"{DOMAIN}_{entry_id}_api_cache",
            )
        else:
            # Config flow validation client: no persistence needed.
            self._cache_loaded = True

    @staticmethod
    def _mask(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if len(value) <= 6:
            return "***"
        return f"{value[:3]}***{value[-3:]}"

    def _redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            redacted: dict[str, Any] = {}
            for key, value in obj.items():
                key_l = key.lower()
                if key_l in {
                    "password",
                    "access_token",
                    "refresh_token",
                    "authorization",
                    "token",
                }:
                    redacted[key] = "***"
                elif key_l in {"username", "mobile", "phone", "sn"}:
                    redacted[key] = self._mask(value)
                else:
                    redacted[key] = self._redact_obj(value)
            return redacted
        if isinstance(obj, list):
            return [self._redact_obj(item) for item in obj]
        if isinstance(obj, str) and len(obj) > 64:
            return self._mask(obj)
        return obj

    def _debug_log(self, message: str, **kwargs: Any) -> None:
        if not self._debug:
            return
        safe_kwargs = {k: self._redact_obj(v) for k, v in kwargs.items()}
        _LOGGER.debug("%s | %s", message, safe_kwargs)

    async def _async_ensure_cache_loaded(self) -> None:
        if self._cache_loaded:
            return
        if self._store is None:
            self._cache_loaded = True
            return
        payload = await self._store.async_load()
        if not isinstance(payload, dict):
            self._cache_loaded = True
            return

        account = payload.get("account")
        if isinstance(account, dict):
            access_token = account.get("access_token")
            refresh_token = account.get("refresh_token")
            expires_at = account.get("expires_at")
            token_checked_at = account.get("token_checked_at")
            devices_last_refresh = account.get("devices_last_refresh")
            devices = account.get("devices")

            if isinstance(access_token, str) and access_token:
                self._access_token = access_token
            if isinstance(refresh_token, str) and refresh_token:
                self._refresh_token = refresh_token
            if isinstance(expires_at, (int, float)):
                self._expires_at = int(expires_at)
            if isinstance(token_checked_at, (int, float)):
                self._token_checked_at = int(token_checked_at)
            if isinstance(devices_last_refresh, (int, float)):
                self._devices_last_refresh = int(devices_last_refresh)
            if isinstance(devices, list):
                self._devices_cache = [d for d in devices if isinstance(d, dict)]

        self._cache_loaded = True

    async def _async_save_cache(self) -> None:
        if self._store is None:
            return
        await self._store.async_save(
            {
                "account": {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                    "expires_at": self._expires_at,
                    "token_checked_at": self._token_checked_at,
                    "devices_last_refresh": self._devices_last_refresh,
                    "devices": self._devices_cache,
                }
            }
        )

    async def async_load_cached_auth(self) -> None:
        await self._async_ensure_cache_loaded()

    async def async_save_cached_auth(self) -> None:
        await self._async_ensure_cache_loaded()
        await self._async_save_cache()

    async def async_load_cached_devices(self) -> None:
        await self._async_ensure_cache_loaded()

    async def async_save_cached_devices(self) -> None:
        await self._async_ensure_cache_loaded()
        await self._async_save_cache()

    async def async_should_refresh_token(self, refresh_interval_hours: int) -> bool:
        await self._async_ensure_cache_loaded()
        now = int(time.time())

        if not self._access_token:
            return True

        if self._expires_at is not None and now >= int(self._expires_at) - 30:
            return True

        interval_seconds = max(1, int(refresh_interval_hours)) * 3600
        if self._token_checked_at is None:
            return True
        return (now - int(self._token_checked_at)) >= interval_seconds

    async def _async_post(
        self,
        *,
        base_url: str,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        req_headers: dict[str, str] = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        try:
            self._debug_log(
                "ninebot request",
                method="POST",
                url=f"{base_url}{path}",
                headers=headers,
                payload=payload,
            )
            response = await self._session.post(
                f"{base_url}{path}",
                json=payload,
                headers=req_headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = await response.json(content_type=None)
            self._debug_log("ninebot response", path=path, response=data)
        except ClientResponseError as err:
            _LOGGER.debug("Ninebot API HTTP error path=%s status=%s", path, err.status)
            raise NinebotApiError(f"HTTP error from Ninebot API: {err.status}") from err
        except ClientError as err:
            raise NinebotConnectionError("Failed to connect to Ninebot cloud") from err

        if not isinstance(data, dict):
            raise NinebotApiError("Ninebot API returned non-object payload")

        return data

    @staticmethod
    def _result_ok(payload: dict[str, Any]) -> bool:
        code = payload.get("resultCode", payload.get("code"))
        if code is None:
            # Some endpoints may omit code while still returning valid payload.
            return True
        try:
            # Ninebot APIs may use 0 or 1 to indicate success depending on endpoint.
            return int(code) in (0, 1)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _result_message(payload: dict[str, Any]) -> str:
        return str(payload.get("resultDesc") or payload.get("desc") or "unknown error")

    @staticmethod
    def _is_auth_error(message: str) -> bool:
        lowered = message.lower()
        return (
            "token" in lowered
            or "auth" in lowered
            or "login" in lowered
            or "password" in lowered
            or "username" in lowered
            or "account" in lowered
            or "登录" in message
            or "认证" in message
            or "密码" in message
            or "账号" in message
            or "用户名" in message
        )

    async def async_login(self) -> None:
        """Authenticate and update local token cache."""
        await self._async_ensure_cache_loaded()
        headers = {
            "clientId": "open_claw_client",
            "timestamp": str(int(time.time() * 1000)),
        }
        payload = {
            "username": self._username,
            "password": self._password,
        }
        response = await self._async_post(
            base_url=LOGIN_BASE_URL,
            path=LOGIN_PATH,
            payload=payload,
            headers=headers,
        )
        data = response.get("data")

        token: str | None = None
        if isinstance(data, dict):
            raw_token = data.get("access_token")
            if isinstance(raw_token, str) and raw_token:
                token = raw_token

        # Follow the reference script behavior: token presence is the source of truth.
        if not isinstance(token, str) or not token:
            message = self._result_message(response)
            if self._is_auth_error(message):
                raise NinebotAuthError(message)
            if not self._result_ok(response):
                raise NinebotApiError(f"Login failed: {message}")
            raise NinebotApiError("Login response missing access_token")

        self._access_token = token
        refresh = data.get("refresh_token") if isinstance(data, dict) else None
        if isinstance(refresh, str) and refresh:
            self._refresh_token = refresh

        validity = data.get("accessTokenValidity") if isinstance(data, dict) else None
        now = int(time.time())
        if isinstance(validity, int):
            self._expires_at = now + validity
        else:
            self._expires_at = None
        self._token_checked_at = now
        await self._async_save_cache()

    async def async_ensure_token(self, *, force: bool = False, refresh_interval_hours: int = 24) -> str:
        """Ensure there is a usable token.

        TODO: Add refresh-token call when public API is available.
        """
        await self._async_ensure_cache_loaded()
        should_refresh = await self.async_should_refresh_token(refresh_interval_hours)

        if not force and not should_refresh and self._access_token:
            return self._access_token

        # Placeholder for refresh-token extension point.
        await self.async_login()
        if not self._access_token:
            raise NinebotAuthError("Login succeeded but no access token was cached")
        return self._access_token

    async def async_should_refresh_devices(self, refresh_interval_hours: int) -> bool:
        await self._async_ensure_cache_loaded()
        if not self._devices_cache:
            return True
        if self._devices_last_refresh is None:
            return True
        interval_seconds = max(1, int(refresh_interval_hours)) * 3600
        return (int(time.time()) - int(self._devices_last_refresh)) >= interval_seconds

    async def async_get_devices(
        self,
        *,
        force_refresh: bool = False,
        refresh_interval_hours: int = 24,
        token_refresh_interval_hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Fetch account devices."""
        await self._async_ensure_cache_loaded()
        if not force_refresh and not await self.async_should_refresh_devices(refresh_interval_hours):
            return copy.deepcopy(self._devices_cache)

        token = await self.async_ensure_token(refresh_interval_hours=token_refresh_interval_hours)
        response = await self._async_post(
            base_url=DEVICE_BASE_URL,
            path=DEVICES_PATH,
            payload={"access_token": token, "lang": self._lang},
        )
        if not self._result_ok(response):
            message = self._result_message(response)
            if self._is_auth_error(message):
                self._access_token = None
                raise NinebotAuthError(message)
            raise NinebotApiError(message)

        data = response.get("data")
        if not isinstance(data, list):
            raise NinebotApiError("Device list response is invalid")

        devices = [item for item in data if isinstance(item, dict)]
        self._devices_cache = devices
        self._devices_last_refresh = int(time.time())
        await self._async_save_cache()
        return copy.deepcopy(devices)

    async def async_get_device_dynamic_info(
        self,
        sn: str,
        *,
        token_refresh_interval_hours: int = 24,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Fetch dynamic state for one device SN."""
        token = access_token or await self.async_ensure_token(refresh_interval_hours=token_refresh_interval_hours)
        response = await self._async_post(
            base_url=DEVICE_BASE_URL,
            path=DEVICE_DYNAMIC_INFO_PATH,
            payload={"access_token": token, "sn": sn},
        )
        if not self._result_ok(response):
            message = self._result_message(response)
            if self._is_auth_error(message):
                self._access_token = None
                raise NinebotAuthError(message)
            raise NinebotApiError(message)

        data = response.get("data")
        if not isinstance(data, dict):
            raise NinebotApiError(f"Dynamic state for {sn} is invalid")
        return data

    async def async_get_multiple_device_dynamic_info(
        self,
        sns: list[str],
        *,
        max_concurrency: int,
        token_refresh_interval_hours: int,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Exception]]:
        """Fetch dynamic state concurrently with concurrency control."""
        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, Exception] = {}
        if not sns:
            return results, errors

        token = await self.async_ensure_token(refresh_interval_hours=token_refresh_interval_hours)
        semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

        async def _fetch(sn: str) -> None:
            async with semaphore:
                try:
                    results[sn] = await self.async_get_device_dynamic_info(
                        sn,
                        token_refresh_interval_hours=token_refresh_interval_hours,
                        access_token=token,
                    )
                except Exception as err:  # noqa: BLE001 - isolate per-device errors
                    errors[sn] = err

        await asyncio.gather(*(_fetch(sn) for sn in sns))
        return results, errors
