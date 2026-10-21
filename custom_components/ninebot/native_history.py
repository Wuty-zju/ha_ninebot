"""Read-only paginated history using an account-owned ninecli session lease.

Fixed ninecli-0.1.7/recon protocol. No password, refresh, token write, vehicle
control or user host override. The calling client owns serialization/refresh.
"""

import base64
import hashlib
import json
import os
import secrets
import stat
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from . import history_crypto as crypto
from .const import MAX_RESPONSE_BYTES
from .exceptions import ErrorKind, NinebotAuthError, NinebotError
from .http_policy import rate_limit_delay
from .parsing import previous_month
from .travel import opaque_id

TRAVEL_BASE = "https://cn-cbu-gateway.ninebot.com"
TRAVEL_PATH = "/app-api/travel/v6/travel-list2"
CONTRACT = "0.1.7+native.history.v1"
CLIENT_VERSION = "610063322"
RN_VERSION = "743"
MAX_PAGE = 4096
MAX_SESSION_BYTES = 64 * 1024


@dataclass(frozen=True, repr=False)
class HistorySession:
    uid: str
    access_token: str
    device_id: str
    fingerprint: str

    def __repr__(self) -> str:
        return "<HistorySession private>"


def _read(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SESSION_BYTES:
            raise ValueError("Session file shape")
        data = stream.read(MAX_SESSION_BYTES + 1)
    if len(data) > MAX_SESSION_BYTES:
        raise ValueError("Session file budget")
    return data


def read_session(directory: Path, expected_uid: str) -> HistorySession:
    """Read only required fields, reject foreign/symlink/oversized sessions."""
    try:
        if directory.is_symlink() or not expected_uid:
            raise ValueError("Session scope")
        for name in ("config.json", "tokens.json"):
            if (directory / name).is_symlink():
                raise ValueError("Session symlink")
        config_raw, token_raw = (_read(directory / name) for name in ("config.json", "tokens.json"))
        config, tokens = json.loads(config_raw), json.loads(token_raw)
        if not isinstance(config, dict) or not isinstance(tokens, dict):
            raise ValueError("Session schema")
        uid = tokens.get("business_uid")
        if type(uid) is int and uid >= 0:
            uid = str(uid)
        token, device = tokens.get("access_token"), config.get("device_id")
        if uid != expected_uid or any(
            not isinstance(value, str)
            or not 1 <= len(value) <= limit
            or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            for value, limit in ((token, 8192), (device, 128), (uid, 256))
        ):
            raise ValueError("Session identity")
        assert isinstance(uid, str) and isinstance(token, str) and isinstance(device, str)
        if tokens.get("region", "bj") != "bj" or config.get("regionx", "bj") != "bj":
            raise NinebotError(ErrorKind.PROTOCOL)
        return HistorySession(
            uid, token, device, hashlib.sha256(config_raw + b"\0" + token_raw).hexdigest()
        )
    except (OSError, ValueError, TypeError, RecursionError):
        raise NinebotAuthError() from None


def validate_page(vehicle: str, month: str, page: int) -> None:
    try:
        previous_month(month)
    except (ValueError, TypeError):
        raise NinebotError(ErrorKind.PROTOCOL) from None
    if not "200001" <= month <= "209912":
        raise NinebotError(ErrorKind.PROTOCOL)
    if not isinstance(vehicle, str) or not vehicle or opaque_id(vehicle) != vehicle:
        raise NinebotError(ErrorKind.PROTOCOL)
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise NinebotError(ErrorKind.PROTOCOL)


def prepare(
    session: HistorySession, vehicle: str, month: str, page: int
) -> tuple[bytes, dict, dict]:
    """Build the verified ordered business request in a bounded worker."""
    validate_page(vehicle, month, page)
    now = int(time.time() * 1000)
    nonce = secrets.token_bytes(16).hex()
    params: dict[str, Any] = dict(
        sys_language="zh-hans-cn",
        client_ver=CLIENT_VERSION,
        device_id=session.device_id,
        regionx="bj",
        language="zh",
        ostype="and",
        lang="zh",
        platform_ver="13 Xiaomi",
        platform="android",
        login_country="CN",
    )
    params.update(
        access_token=session.access_token,
        uid=session.uid,
        wnumber=vehicle,
        rnVersion=RN_VERSION,
        vehicle_type="112",
        month=month,
        page=str(page),
        current_version=CLIENT_VERSION,
        serviceTime=now,
        nonce=nonce[:2].lower() + nonce[2:].upper(),
    )
    wire, keys = crypto.build_request(crypto.build_inner(params).encode())
    headers = {
        "User-Agent": "okhttp/4.9.1",
        "Accept-Encoding": "gzip",
        "Business-Type": "2",
        "Cache-Control": "no-cache",
        "Client-Ver": CLIENT_VERSION,
        "Content-Type": "text/html;charset=UTF-8",
        "Debug": "0",
        "Device-Id": session.device_id,
        "Language": "zh",
        "Login-Country": "CN",
        "Need_decrypt": "1",
        "Ninebot-Version": "2",
        "Platform": "Android",
        "Platform-Ver": "13",
        "Regionx": "bj",
        "Request-Id": secrets.token_hex(16),
        "Service-Time": str(now),
        "Sys-Language": "zh-hans-cn",
        "Access_token": session.access_token,
        "Accept": "application/json",
        "Rn-Module": "Track",
        "Rn-Ver": RN_VERSION,
        "Rn-Version": "0",
    }
    return json.dumps(wire, separators=(",", ":")).encode(), keys, headers


def decode(body: bytes, keys: dict, encoding: str | None = None) -> dict[str, Any]:
    try:
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Response budget")
        if encoding == "gzip":
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = inflater.decompress(body, MAX_RESPONSE_BYTES + 1)
            if (
                len(body) > MAX_RESPONSE_BYTES
                or not inflater.eof
                or inflater.unused_data
                or inflater.unconsumed_tail
            ):
                raise ValueError("Compression budget")
        elif encoding not in (None, "identity"):
            raise ValueError("Unsupported compression")
        outer = json.loads(body)
        if not isinstance(outer, dict) or not isinstance(outer.get("r"), str):
            raise ValueError("Wire shape")
        # Strict base64 and block length before entering the pinned decoder.
        cipher = base64.b64decode(outer["r"], validate=True)
        if not cipher or len(cipher) % 16:
            raise ValueError("Cipher shape")
        plain = crypto.decrypt_wire_response(outer, keys)
        if len(plain) > MAX_RESPONSE_BYTES:
            raise ValueError("Plain budget")
        response = json.loads(plain)
        if not isinstance(response, dict) or type(response.get("code")) is not int:
            raise ValueError("Business shape")
        if response["code"] not in (0, 1):
            # Unknown business numbers/prose do not prove invalid credentials.
            raise NinebotError(ErrorKind.SERVICE)
        if not isinstance(response.get("data"), dict):
            raise ValueError("Data shape")
        return response["data"]
    except (ValueError, TypeError, KeyError, RecursionError, zlib.error):
        raise NinebotError(ErrorKind.PROTOCOL) from None


async def request_page(
    http: aiohttp.ClientSession,
    lease: HistorySession,
    vehicle: str,
    month: str,
    page: int,
    deadline_seconds: float,
) -> dict[str, Any]:
    import asyncio

    wire, keys, headers = await asyncio.to_thread(prepare, lease, vehicle, month, page)
    try:
        async with http.post(
            TRAVEL_BASE + TRAVEL_PATH,
            data=wire,
            headers=headers,
            proxy=None,
            allow_redirects=False,
            auto_decompress=False,
            ssl=True,
            timeout=aiohttp.ClientTimeout(total=deadline_seconds),
        ) as response:
            body_parts = bytearray()
            async for part in response.content.iter_chunked(16384):
                if len(body_parts) + len(part) > MAX_RESPONSE_BYTES:
                    raise NinebotError(ErrorKind.PROTOCOL)
                body_parts.extend(part)
            body = bytes(body_parts)
            encoding = response.headers.get("Content-Encoding")
            if response.status == 401:
                raise NinebotAuthError()
            if response.status == 429:
                raise NinebotError(
                    ErrorKind.SERVICE,
                    retry_after=rate_limit_delay(
                        response.headers.get("Retry-After"), datetime.now(UTC)
                    ),
                    retryable=False,
                )
            if response.status != 200:
                raise NinebotError(ErrorKind.SERVICE, retryable=response.status >= 500)
        return await asyncio.to_thread(decode, body, keys, encoding)
    except (aiohttp.ClientError, TimeoutError):
        raise NinebotError(ErrorKind.CONNECTION, retryable=True) from None
