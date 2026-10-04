"""Instrumented native crypto conformance, all upstreams loopback and fake.

Only a temporary copy's public-key asset and macOS signature change. This is
not untouched-binary evidence or a real cloud/physical-control test. The key
derivation/public-key vector is adapted from kxn/ninebot-recon a46124d (MIT;
copyright 2026 kxn). Full notice: tests/ninebot_recon_license.txt.
"""

import asyncio
import base64
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import aiohttp
import ninecli
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.exceptions import ErrorKind, NinebotError

pytestmark = pytest.mark.usefixtures("socket_enabled")

PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDT3m0c/8y9c13PzaFbATEg+Zwd
kpPcCy0V21VKBBSx16ckVtLERAQ7EH8d6DqgEbyzayAwlQd1gDhUmx27hDWafXr9
/evUZkkBegcsNnKrIlh93lPKccjk+LDXS1TnDIIFiTlSbNnaYwehI/9pUbKCI3h7
yE0pum6hJh/9QtGPlwIDAQAB
-----END PUBLIC KEY-----"""


def roll(value, bits):
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def response_key(parts):
    values = []
    for part in parts:
        value = 0
        for byte in part.encode():
            value = roll(value, 8) ^ byte
        values.append(value)
    a, b, c, d = values
    x, y = a ^ c, b ^ d
    fx, gy = roll(x, 8) ^ x ^ roll(x, 24), roll(y, 8) ^ y ^ roll(y, 24)
    a, b, c, d = a ^ gy, b ^ fx, c ^ gy, d ^ fx
    out3 = (c & b) ^ a
    temporary = (~(c ^ d ^ ((c | d) ^ b))) & 0xFFFFFFFF
    out2 = out3 ^ temporary
    out1 = ((temporary | out3) ^ ((c | d) ^ b)) & 0xFFFFFFFF
    out0 = ((~((c | d) ^ b) & out2) ^ d) & 0xFFFFFFFF
    return struct.pack("<IIII", out0, out1, out2, out3)


def aes(key, value, *, encrypt):
    cipher = Cipher(algorithms.AES(key), modes.CBC(bytes(16)))
    if encrypt:
        pad = padding.PKCS7(128).padder()
        value = pad.update(value) + pad.finalize()
        operation = cipher.encryptor()
        return operation.update(value) + operation.finalize()
    operation = cipher.decryptor()
    value = operation.update(value) + operation.finalize()
    pad = padding.PKCS7(128).unpadder()
    return pad.update(value) + pad.finalize()


async def test_native_encrypted_acceptance_and_denial_are_not_physical_outcomes(
    tmp_path, monkeypatch
):
    source = Path(ninecli.__file__).parent / "bin/ninecli"
    original = source.read_bytes()
    original_hash = hashlib.sha256(original).digest()
    vendor = serialization.load_pem_public_key(PUBLIC_KEY)
    encoded_vendor = vendor.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.PKCS1
    )
    assert original.count(encoded_vendor) == 1
    private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    replacement = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.PKCS1
    )
    assert len(replacement) == len(encoded_vendor)
    binary = tmp_path / "instrumented-test-only-ninecli"
    binary.write_bytes(original.replace(encoded_vendor, replacement, 1))
    binary.chmod(0o700)
    execute = asyncio.create_subprocess_exec
    if sys.platform == "darwin":
        signature = await execute(
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            str(binary),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert await signature.wait() == 0
    session_dir = tmp_path / "synthetic-session"
    session_dir.mkdir(mode=0o700)
    (session_dir / "tokens.json").write_text(
        json.dumps(
            {
                "uuid": "synthetic-uuid",
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "accessTokenValidity": "4102444800000",
                "business_uid": "123",
                "saved_at": int(time.time()),
            }
        )
    )
    paths = []
    reply_code = 0

    async def handler(request):
        body = await request.json()
        assert set(body) == {"d", "h", "k", "p", "t"}
        key = private.decrypt(base64.b64decode(body["k"]), rsa_padding.PKCS1v15())
        wrapper_bytes = aes(key, base64.b64decode(body["d"]), encrypt=False)
        assert len(key) == 16
        assert hashlib.md5(wrapper_bytes).hexdigest() == body["h"]
        wrapper = json.loads(wrapper_bytes)
        business = json.loads(base64.b64decode(wrapper["data"]))
        assert business["access_token"] == "synthetic-access"
        assert isinstance(business["cmd"], str)
        paths.append(request.path)
        key = response_key(
            [
                wrapper[name]
                for name in (
                    "keyDataOne",
                    "keyDataTwo",
                    "keyDataThree",
                    "keyDataFour",
                )
            ]
        )
        # accepted is a synthetic test marker, not an observed cloud field.
        plain = json.dumps({"code": reply_code, "data": {"accepted": reply_code == 0}}).encode()
        response = json.dumps({"data": base64.b64encode(plain).decode()}).encode()
        return web.json_response({"r": base64.b64encode(aes(key, response, encrypt=True)).decode()})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    async with TestServer(app, host="127.0.0.1") as server:
        origin = str(server.make_url("/")).rstrip("/")

        async def spawn(*args, **kwargs):
            assert args[1:3] == ("-m", "ninecli") and "serve" in args
            # All five native origins are explicitly local, before any query.
            flags = tuple(
                item
                for flag in (
                    "--passport-base",
                    "--biz-host",
                    "--ebike-host",
                    "--motor-host",
                    "--travel-host",
                )
                for item in (flag, origin)
            )
            return await execute(str(binary), *args[3:5], *flags, *args[5:], **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        async with aiohttp.ClientSession(trust_env=False) as session:
            client = NinecliClient(session_dir, session, timeout=5)
            try:
                for action in ("bell", "buck", "engine/start", "engine/stop"):
                    assert await client.async_control("synthetic-vehicle", action) is None
                reply_code = 403
                with pytest.raises(NinebotError) as failure:
                    await client.async_control("synthetic-vehicle", "bell")
                assert failure.value.kind is ErrorKind.SERVICE
            finally:
                await client.async_close()
            assert client._process is None
    assert paths == [
        "/devices/control/bell",
        "/devices/control/open_buck",
        "/devices/control/engine_start",
        "/devices/control/engine_stop",
        "/devices/control/bell",
    ]
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash
