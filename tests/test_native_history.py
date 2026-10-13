"""Synthetic encrypted peers and credentials only; no actual Ninebot cloud."""

import asyncio
import base64
import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pytest_socket import enable_socket, socket_allow_hosts

from custom_components.ninebot import history_crypto as crypto
from custom_components.ninebot import native_history as native
from custom_components.ninebot.backend import NinecliBackend
from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError
from custom_components.ninebot.raw import build_record

UID = "synthetic-business"


@pytest.fixture(autouse=True)
def loopback_peer_only(socket_enabled):
    """Allow the synthetic localhost peer, while keeping Internet blocked."""
    enable_socket()
    socket_allow_hosts(["127.0.0.1"])


def credentials(directory: Path, uid=UID, token="synthetic-access"):
    directory.mkdir(exist_ok=True)
    (directory / "config.json").write_text(json.dumps({"device_id": "synthetic-device"}))
    (directory / "tokens.json").write_text(json.dumps({"business_uid": uid, "access_token": token}))


@pytest.fixture
def peer_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    monkeypatch.setattr(crypto, "RSA_PUBLIC_KEY_PEM", pem)
    return key


def unwrap(body, key):
    aes = key.decrypt(base64.b64decode(body["k"]), padding.PKCS1v15())
    plain = crypto._aes_cbc_decrypt(aes, base64.b64decode(body["d"]))
    assert hashlib.md5(plain).hexdigest() == body["h"]
    wrapper = json.loads(plain)
    params = json.loads(crypto.unwrap_response(plain))
    original = {k: v for k, v in params.items() if k != "checkcode"}
    # Independent standard JSON/MD5 comparison; these synthetic values are ASCII.
    assert (
        params["checkcode"]
        == hashlib.md5(json.dumps(original, separators=(",", ":")).encode()).hexdigest().upper()
    )
    assert wrapper["platform"] == 2
    return params, wrapper


def encrypted(response, wrapper):
    plain = json.dumps({"data": crypto.android_b64(json.dumps(response).encode())}).encode()
    key = crypto.derive_key(
        *(wrapper[f"keyData{name}"].encode() for name in ("One", "Two", "Three", "Four"))
    )
    return {"r": base64.b64encode(crypto._aes_cbc_encrypt(key, plain)).decode()}


async def test_real_crypto_pages_are_distinct_bound_and_do_not_write_session(
    tmp_path, peer_key, monkeypatch
):
    credentials(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    seen = []

    async def handle(request):
        params, wrapper = unwrap(await request.json(), peer_key)
        seen.append(params)
        assert request.headers["Access_token"] == params["access_token"]
        assert params["uid"] == UID and params["wnumber"] == "synthetic-vehicle"
        return web.json_response(
            encrypted(
                {
                    "code": 0,
                    "data": {"times": 2, "list": [{"travel_id": f"page-{params['page']}"}]},
                },
                wrapper,
            )
        )

    app = web.Application()
    app.router.add_post(native.TRAVEL_PATH, handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        monkeypatch.setattr(native, "TRAVEL_BASE", str(server.make_url("/")).rstrip("/"))
        client = NinecliClient(tmp_path, http)
        backend = NinecliBackend(client, expected_uid=UID)
        first = await backend.async_travel_page("synthetic-vehicle", "202610", 1)
        second = await backend.async_travel_page("synthetic-vehicle", "202610", 2)
        assert first.payload["list"] != second.payload["list"]
        assert [r.page for r in (first, second)] == [1, 2]
        assert all(
            r.provider == "native_history" and r.endpoint_version == native.CONTRACT
            for r in (first, second)
        )
        retained = build_record(
            first.endpoint,
            first.payload,
            first.received_at,
            first.query_month,
            backend_version=first.backend_version,
            endpoint_version=first.endpoint_version,
        )
        assert retained.endpoint_version == native.CONTRACT
        await client.async_close()
    assert [x["page"] for x in seen] == ["1", "2"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


async def test_http401_only_refreshes_via_owner_once(tmp_path, peer_key, monkeypatch):
    credentials(tmp_path)
    calls = []

    async def handle(request):
        params, wrapper = unwrap(await request.json(), peer_key)
        calls.append(params["access_token"])
        if len(calls) == 1:
            return web.Response(status=401)
        return web.json_response(encrypted({"code": 0, "data": {"times": 0, "list": []}}, wrapper))

    app = web.Application()
    app.router.add_post(native.TRAVEL_PATH, handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        monkeypatch.setattr(native, "TRAVEL_BASE", str(server.make_url("/")).rstrip("/"))
        client = NinecliClient(tmp_path, http)

        async def owner(method, path):
            assert (method, path) == ("POST", "/auth/refresh")
            credentials(tmp_path, token="synthetic-rotated")
            return {}

        client._request_locked = AsyncMock(side_effect=owner)
        await client.async_get_travel_page("synthetic-vehicle", "202610", 1, expected_uid=UID)
        client._request_locked.assert_awaited_once()
        await client.async_close()
    assert calls == ["synthetic-access", "synthetic-rotated"]


@pytest.mark.parametrize("rotation,kind", [(UID, ErrorKind.BUSY), ("foreign", ErrorKind.AUTH)])
async def test_rotated_or_foreign_session_cannot_return_or_refresh(
    tmp_path, peer_key, monkeypatch, rotation, kind
):
    credentials(tmp_path)

    async def handle(request):
        _, wrapper = unwrap(await request.json(), peer_key)
        credentials(tmp_path, uid=rotation, token="changed")
        return web.json_response(encrypted({"code": 0, "data": {"list": []}}, wrapper))

    app = web.Application()
    app.router.add_post(native.TRAVEL_PATH, handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        monkeypatch.setattr(native, "TRAVEL_BASE", str(server.make_url("/")).rstrip("/"))
        client = NinecliClient(tmp_path, http)
        client._request_locked = AsyncMock()
        with pytest.raises(NinebotError) as err:
            await client.async_get_travel_page("synthetic-vehicle", "202610", 1, expected_uid=UID)
        assert err.value.kind is kind
        client._request_locked.assert_not_awaited()
        await client.async_close()


async def test_native_and_serve_share_lock_and_lifecycle(tmp_path, peer_key, monkeypatch):
    credentials(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def handle(request):
        _, wrapper = unwrap(await request.json(), peer_key)
        started.set()
        await release.wait()
        return web.json_response(encrypted({"code": 0, "data": {"list": []}}, wrapper))

    app = web.Application()
    app.router.add_post(native.TRAVEL_PATH, handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        monkeypatch.setattr(native, "TRAVEL_BASE", str(server.make_url("/")).rstrip("/"))
        client = NinecliClient(tmp_path, http)
        client._request_locked = AsyncMock(return_value={})
        task = asyncio.create_task(
            client.async_get_travel_page("synthetic-vehicle", "202610", 1, expected_uid=UID)
        )
        await started.wait()
        status = asyncio.create_task(client.async_get_status_once("synthetic-vehicle"))
        await asyncio.sleep(0)
        client._request_locked.assert_not_awaited()
        release.set()
        await asyncio.gather(task, status)
        client._request_locked.assert_awaited_once()
        await client.async_close()
        with pytest.raises(NinebotError) as err:
            await client.async_get_travel_page("synthetic-vehicle", "202610", 1, expected_uid=UID)
        assert err.value.kind is ErrorKind.CLOSED


@pytest.mark.parametrize("case", ["foreign", "symlink", "large", "invalid", "missing"])
def test_session_reader_fails_closed_without_writes(tmp_path, case):
    credentials(tmp_path)
    file = tmp_path / "tokens.json"
    if case == "foreign":
        credentials(tmp_path, uid="foreign")
    if case == "symlink":
        file.rename(tmp_path / "original.json")
        file.symlink_to(tmp_path / "original.json")
    if case == "large":
        file.write_bytes(b"x" * (native.MAX_SESSION_BYTES + 1))
    if case == "invalid":
        file.write_text("[]")
    if case == "missing":
        file.unlink()
    with pytest.raises(NinebotAuthError):
        native.read_session(tmp_path, UID)


@pytest.mark.parametrize("page", [0, -1, True, 1.5, native.MAX_PAGE + 1])
def test_page_input_and_secret_repr(tmp_path, page):
    credentials(tmp_path)
    session = native.read_session(tmp_path, UID)
    assert "synthetic" not in repr(session)
    with pytest.raises(NinebotError):
        native.validate_page("synthetic-vehicle", "202610", page)


def test_nist_aes_first_block_and_ordered_json():
    # NIST SP800-38A ECB vector: zero IV makes the first CBC block identical.
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    data = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    assert crypto._aes_cbc_encrypt(key, data)[:16].hex() == "3ad77bb40d7a3660a89ecaf32466ef97"
    assert crypto.android_b64(b"hello") == "aGVsbG8=\n"
    inner = crypto.build_inner({"z": "<&>", "a": 1})
    expected = hashlib.md5(b'{"z":"<&>","a":1}').hexdigest().upper()
    assert inner == '{"z":"<&>","a":1,"checkcode":"' + expected + '"}'


@pytest.mark.parametrize("value", [b"{}", b'{"r":"bad!"}', b'{"r":""}', b"[]"])
def test_invalid_wire_is_sanitized(value):
    with pytest.raises(NinebotError) as err:
        native.decode(value, {})
    assert err.value.kind is ErrorKind.PROTOCOL


@pytest.mark.parametrize(
    "status,kind", [(429, ErrorKind.SERVICE), (302, ErrorKind.SERVICE), (500, ErrorKind.SERVICE)]
)
async def test_transport_errors_never_refresh_or_follow_redirects(
    tmp_path, monkeypatch, status, kind
):
    credentials(tmp_path)

    async def handle(request):
        return web.Response(
            status=status,
            headers={"Retry-After": "120", "Location": "https://example.invalid/secret"},
        )

    app = web.Application()
    app.router.add_post(native.TRAVEL_PATH, handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        monkeypatch.setattr(native, "TRAVEL_BASE", str(server.make_url("/")).rstrip("/"))
        client = NinecliClient(tmp_path, http)
        client._request_locked = AsyncMock()
        with pytest.raises(NinebotError) as err:
            await client.async_get_travel_page("synthetic", "202610", 1, expected_uid=UID)
        assert err.value.kind is kind
        if status == 429:
            assert err.value.retry_after == 120
        client._request_locked.assert_not_awaited()
        await client.async_close()


def test_gzip_decompression_and_plaintext_budgets():
    with pytest.raises(NinebotError):
        native.decode(gzip.compress(b"x" * (native.MAX_RESPONSE_BYTES + 1)), {}, "gzip")
    with pytest.raises(NinebotError):
        native.decode(b"{}", {}, "br")


async def test_gzip_streamed_wire_read_is_complete(tmp_path, peer_key, monkeypatch):
    credentials(tmp_path)

    async def handle(request):
        _, wrapper = unwrap(await request.json(), peer_key)
        wire = gzip.compress(
            json.dumps(encrypted({"code": 0, "data": {"list": []}}, wrapper)).encode()
        )
        response = web.StreamResponse(headers={"Content-Encoding": "gzip"})
        await response.prepare(request)
        for start in range(0, len(wire), 7):
            await response.write(wire[start : start + 7])
            await asyncio.sleep(0)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post(native.TRAVEL_PATH, handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        monkeypatch.setattr(native, "TRAVEL_BASE", str(server.make_url("/")).rstrip("/"))
        client = NinecliClient(tmp_path, http)
        result = await client.async_get_travel_page("synthetic", "202610", 1, expected_uid=UID)
        assert result == {"list": []}
        await client.async_close()
