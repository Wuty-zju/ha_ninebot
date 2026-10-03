import asyncio
import json
import stat
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.session import Candidate, SessionManager, session_uid


def write(path, uid="fake-uid"):
    path.mkdir(exist_ok=True)
    (path / "tokens.json").write_text(
        json.dumps({"business_uid": uid, "access_token": "synthetic"})
    )


async def test_validate_isolated_then_commit_rollback(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    key = "a" * 32
    dest = root / key
    write(dest, "prior-uid")
    client = AsyncMock()

    def factory(path, session):
        write(path, "fake-uid")
        return client

    client.async_list_vehicles.return_value = []
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http, factory)
        candidate = await manager.async_prepare("fake", "password")
        assert session_uid(dest) == "prior-uid"
        assert candidate.uid == "fake-uid"
        assert stat.S_IMODE(candidate.path.stat().st_mode) == 0o700
        assert stat.S_IMODE((candidate.path / "tokens.json").stat().st_mode) == 0o600
        await manager.async_commit(candidate, key)
        assert session_uid(dest) == "fake-uid"
        await manager.async_rollback(key)
        assert session_uid(dest) == "prior-uid"


async def test_failed_login_or_cancel_preserves_formal_session(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    key = "b" * 32
    write(root / key, "prior")
    for failure in (NinebotError(ErrorKind.CONNECTION), asyncio.CancelledError()):
        client = AsyncMock()
        client.async_login.side_effect = failure
        async with aiohttp.ClientSession() as http:
            manager = SessionManager(root, http, lambda path, s, client=client: client)
            with pytest.raises(type(failure)):
                await manager.async_prepare("fake", "password")
            assert session_uid(root / key) == "prior"
            assert list(root.glob(".candidate-*")) == []
            client.async_close.assert_awaited()


async def test_commit_failure_and_crash_recovery(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    key = "c" * 32
    dest = root / key
    write(dest, "prior")
    stage = root / ".candidate-test"
    write(stage, "candidate")
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        import os

        real = os.replace

        def rename(src, dst):
            if src == stage:
                raise OSError("fake disk failure")
            real(src, dst)

        with patch("custom_components.ninebot.session.os.replace", side_effect=rename):
            with pytest.raises(OSError):
                await manager.async_commit(Candidate(stage, "candidate"), key)
        assert session_uid(dest) == "prior"
        await manager.async_rollback(key)
        assert session_uid(dest) == "prior"
        real(dest, root / f".backup-{key}")
        await manager.async_recover(key)
        assert session_uid(dest) == "prior"
        write(stage, "candidate")
        await manager.async_commit(Candidate(stage, "candidate"), key)
        await manager.async_finalize(key)
        assert not (root / f".backup-{key}").exists()


async def test_import_is_copy_and_rejects_identity_or_path(tmp_path):
    source = tmp_path / "v1"
    write(source)
    root = tmp_path / "private"
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        key, candidate = await manager.async_import(source, "fake-uid")
        await manager.async_commit(candidate, key)
        await manager.async_finalize(key)
        assert (source / "tokens.json").exists()
        assert session_uid(manager.path(key)) == "fake-uid"
        with pytest.raises(NinebotError):
            manager.path("../escape")
        with pytest.raises(NinebotError):
            await manager.async_import(source, "different")
        assert session_uid(source) == "fake-uid"
