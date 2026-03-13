"""Async Ninebot cloud API client."""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import (
    DEFAULT_TIMEOUT_SECONDS,
    DEVICE_BASE_URL,
    DEVICE_DYNAMIC_INFO_PATH,
    DEVICES_PATH,
    LOGIN_BASE_URL,
    LOGIN_PATH,
)
from .exceptions import (
    NinebotApiError,
    NinebotAuthError,
    NinebotConnectionError,
)

_LOGGER = logging.getLogger(__name__)


class NinebotApiClient:
    """Ninebot API client wrapper used by coordinator and config flow."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        lang: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._lang = lang

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: int | None = None

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
            response = await self._session.post(
                f"{base_url}{path}",
                json=payload,
                headers=req_headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = await response.json(content_type=None)
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
            or "登录" in message
            or "认证" in message
        )

    async def async_login(self) -> None:
        """Authenticate and update local token cache."""
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

        if not self._result_ok(response):
            raise NinebotAuthError(self._result_message(response))

        data = response.get("data")
        if not isinstance(data, dict):
            raise NinebotAuthError("Missing login response data")

        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise NinebotAuthError("Missing access token in login response")

        self._access_token = token
        refresh = data.get("refresh_token")
        if isinstance(refresh, str) and refresh:
            self._refresh_token = refresh

        validity = data.get("accessTokenValidity")
        if isinstance(validity, int):
            self._expires_at = int(time.time()) + validity
        else:
            self._expires_at = None

    async def async_ensure_token(self) -> str:
        """Ensure there is a usable token.

        TODO: Add refresh-token call when public API is available.
        """
        if self._access_token and self._expires_at:
            # Keep 30 seconds margin to avoid expiry during requests.
            if int(time.time()) < self._expires_at - 30:
                return self._access_token

        if self._access_token and self._expires_at is None:
            return self._access_token

        await self.async_login()
        if not self._access_token:
            raise NinebotAuthError("Login succeeded but no access token was cached")
        return self._access_token

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Fetch account devices."""
        token = await self.async_ensure_token()
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

        return [item for item in data if isinstance(item, dict)]

    async def async_get_device_dynamic_info(self, sn: str) -> dict[str, Any]:
        """Fetch dynamic state for one device SN."""
        token = await self.async_ensure_token()
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
