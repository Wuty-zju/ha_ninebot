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


@pytest.mark.parametrize("previous", [False, True])
async def test_unfinalized_commit_is_recovered_after_restart(tmp_path, previous):
    root = tmp_path / "private"
    root.mkdir()
    key = "d" * 32
    destination = root / key
    if previous:
        write(destination, "prior")
    stage = root / ".candidate-restart"
    write(stage, "new")
    async with aiohttp.ClientSession() as http:
        first = SessionManager(root, http)
        await first.async_commit(Candidate(stage, "new"), key)
        assert session_uid(destination) == "new"
        restarted = SessionManager(root, http)
        await restarted.async_recover(key)
        if previous:
            assert session_uid(destination) == "prior"
        else:
            assert not destination.exists()
        assert not list(root.glob(".*"))
        assert await restarted.async_rollback(key) is False


async def test_live_transaction_reload_does_not_undo_candidate(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    key = "d" * 32
    write(root / key, "prior")
    stage = root / ".candidate-reload"
    write(stage, "new")
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        async with manager.transaction(key):
            await manager.async_commit(Candidate(stage, "new"), key)
            await manager.async_recover(key)
            assert session_uid(root / key) == "new"
            assert session_uid(root / f".backup-{key}") == "prior"
            await manager.async_finalize(key)
        await manager.async_recover(key)
        assert session_uid(root / key) == "new"
        assert not list(root.glob(".*"))


async def test_entry_transactions_are_serialized_and_cancelled_waiter_releases(tmp_path):
    root = tmp_path / "private"
    entered = asyncio.Event()
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        key = "e" * 32

        async def waiter():
            async with manager.transaction(key):
                entered.set()

        async with manager.transaction(key):
            task = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            assert not entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await waiter()
        assert entered.is_set()
        assert not manager._live_transactions


async def test_cancel_during_directory_creation_waits_then_cleans(tmp_path):
    import threading

    started, release = threading.Event(), threading.Event()
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(tmp_path / "private", http)
        original = manager._candidate_dir

        def create():
            path = original()
            started.set()
            assert release.wait(5)
            return path

        with patch.object(manager, "_candidate_dir", side_effect=create):
            task = asyncio.create_task(manager.async_prepare("fake", "synthetic"))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not list(manager.root.glob(".candidate-*"))


async def test_cancel_during_permission_worker_waits_before_cleanup(tmp_path):
    import threading

    from custom_components.ninebot.session import secure_files

    started, release = threading.Event(), threading.Event()
    client = AsyncMock()
    client.async_list_vehicles.return_value = []
    paths = []

    def factory(path, session):
        write(path)
        paths.append(path)
        return client

    def secure(path):
        secure_files(path)
        started.set()
        assert release.wait(5)
        assert path.exists()

    async with aiohttp.ClientSession() as http:
        manager = SessionManager(tmp_path / "private", http, factory)
        with patch("custom_components.ninebot.session.secure_files", side_effect=secure):
            task = asyncio.create_task(manager.async_prepare("fake", "synthetic"))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert paths[0].exists()
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not paths[0].exists()


async def test_cancel_during_commit_restores_previous_session(tmp_path):
    import os
    import threading

    root = tmp_path / "private"
    root.mkdir()
    key = "f" * 32
    write(root / key, "prior")
    candidate = root / ".candidate-cancel"
    write(candidate, "new")
    started, release = threading.Event(), threading.Event()
    original = os.replace

    def replace(src, dst):
        original(src, dst)
        if src == candidate:
            started.set()
            assert release.wait(5)

    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        with patch("custom_components.ninebot.session.os.replace", side_effect=replace):
            task = asyncio.create_task(manager.async_commit(Candidate(candidate, "new"), key))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert session_uid(root / f".backup-{key}") == "prior"
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert session_uid(root / key) == "prior"
        assert not list(root.glob(".*"))


async def test_cancel_import_waits_for_copy_and_removes_candidate(tmp_path):
    import shutil
    import threading

    source = tmp_path / "v1"
    write(source)
    before = (source / "tokens.json").read_bytes()
    started, release = threading.Event(), threading.Event()
    original = shutil.copyfile

    def copy(src, dst):
        original(src, dst)
        started.set()
        assert release.wait(5)

    async with aiohttp.ClientSession() as http:
        manager = SessionManager(tmp_path / "private", http)
        with patch("custom_components.ninebot.session.shutil.copyfile", side_effect=copy):
            task = asyncio.create_task(manager.async_import(source, "fake-uid"))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert (source / "tokens.json").read_bytes() == before
        assert not list(manager.root.glob(".candidate-*"))


@pytest.mark.parametrize("record", ["not-json", "[]", '{"had_previous":1}'])
async def test_invalid_journal_preserves_formal_session(tmp_path, record):
    root = tmp_path / "private"
    root.mkdir()
    key = "a" * 32
    write(root / key, "prior")
    journal = root / f".transaction-{key}.json"
    journal.write_text(record)
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        with pytest.raises(NinebotError) as error:
            await manager.async_recover(key)
    assert error.value.kind == ErrorKind.PROTOCOL
    assert session_uid(root / key) == "prior"
    assert journal.read_text() == record


async def test_symlink_import_or_commit_cannot_access_outside_session(tmp_path):
    outside = tmp_path / "outside"
    write(outside)
    source = tmp_path / "v1"
    write(source)
    (source / "config.json").symlink_to(outside / "tokens.json")
    root = tmp_path / "private"
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        with pytest.raises(NinebotError):
            await manager.async_import(source, "fake-uid")
        assert not list(root.glob(".candidate-*"))
        link = root / ".candidate-link"
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(NinebotError):
            await manager.async_commit(Candidate(link, "fake-uid"), "b" * 32)
        with pytest.raises(NinebotError):
            session_uid(link)
        assert session_uid(outside) == "fake-uid"


async def test_factory_failure_cleans_candidate(tmp_path):
    def fail(path, session):
        raise OSError("synthetic-start-failure")

    async with aiohttp.ClientSession() as http:
        manager = SessionManager(tmp_path / "private", http, fail)
        with pytest.raises(OSError):
            await manager.async_prepare("fake", "synthetic")
        assert not list(manager.root.glob(".candidate-*"))


async def test_finalize_cancel_waits_and_preserves_accepted_session(tmp_path):
    import shutil
    import threading

    root = tmp_path / "private"
    root.mkdir()
    key = "a" * 32
    write(root / key, "prior")
    candidate = root / ".candidate-finalize"
    write(candidate, "new")
    started, release = threading.Event(), threading.Event()
    original = shutil.rmtree

    def remove(path, **kwargs):
        started.set()
        assert release.wait(5)
        original(path, **kwargs)

    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        await manager.async_commit(Candidate(candidate, "new"), key)
        with patch("custom_components.ninebot.session.shutil.rmtree", side_effect=remove):
            task = asyncio.create_task(manager.async_finalize(key))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not await manager.async_is_pending(key)
        assert await manager.async_rollback(key) is False
        assert session_uid(root / key) == "new"
        assert not list(root.glob(".*"))


@pytest.mark.parametrize("kind", ["wrong-parent", "changed-uid", "non-candidate"])
async def test_commit_validates_candidate_identity_and_location(tmp_path, kind):
    root = tmp_path / "private"
    root.mkdir()
    key = "b" * 32
    write(root / key, "prior")
    path = tmp_path / ".candidate-external" if kind == "wrong-parent" else root / ".candidate-test"
    if kind == "non-candidate":
        path = root / "other-directory"
    write(path, "changed" if kind == "changed-uid" else "new")
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        with pytest.raises(NinebotError):
            await manager.async_commit(Candidate(path, "new"), key)
        assert session_uid(root / key) == "prior"
        assert not await manager.async_is_pending(key)


@pytest.mark.parametrize("raw", ["{}", "[]", '{"business_uid":5}', "bad-json"])
def test_session_identity_requires_valid_business_login(tmp_path, raw):
    (tmp_path / "tokens.json").write_text(raw)
    with pytest.raises(NinebotError) as error:
        session_uid(tmp_path)
    assert error.value.kind == ErrorKind.PROTOCOL


async def test_private_root_and_candidate_files_reject_symlinks_or_directories(tmp_path):
    from custom_components.ninebot.session import private_directory, secure_files

    outside = tmp_path / "outside"
    write(outside)
    link = tmp_path / "private"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(NinebotError):
        private_directory(link)
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "nested").mkdir()
    with pytest.raises(NinebotError):
        secure_files(folder)
    key = "c" * 32
    root = tmp_path / "sessions"
    root.mkdir()
    (root / key).symlink_to(outside, target_is_directory=True)
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(root, http)
        with pytest.raises(NinebotError):
            await manager.async_recover(key)
    assert session_uid(outside) == "fake-uid"


async def test_recover_accepted_destination_cleans_prejournal_backup(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    key = "d" * 32
    write(root / key, "accepted")
    write(root / f".backup-{key}", "prior")
    async with aiohttp.ClientSession() as http:
        await SessionManager(root, http).async_recover(key)
    assert session_uid(root / key) == "accepted"
    assert not (root / f".backup-{key}").exists()


@pytest.mark.parametrize(
    "failure", [None, ErrorKind.CONNECTION, ErrorKind.PROTOCOL, ErrorKind.AUTH]
)
async def test_account_display_read_is_optional_except_auth_and_isolated(tmp_path, failure):
    client = AsyncMock()
    client.async_list_vehicles.return_value = []
    client.async_get_account.return_value = {
        "username": "Rider",
        "region": "bj",
        "phone": "private",
        "token": "secret",
    }
    if failure:
        client.async_get_account.side_effect = NinebotError(failure)

    def factory(path, session):
        write(path)
        return client

    async with aiohttp.ClientSession() as http:
        manager = SessionManager(tmp_path / "private", http, factory)
        if failure is ErrorKind.AUTH:
            with pytest.raises(NinebotError):
                await manager.async_prepare("test-account", "synthetic")
            assert not list(manager.root.glob(".candidate-*"))
            client.async_list_vehicles.assert_not_awaited()
        else:
            candidate = await manager.async_prepare("test-account", "synthetic")
            assert candidate.display.as_dict() == (
                {} if failure else {"username": "Rider", "region": "bj"}
            )
            await manager.async_discard(candidate)
        client.async_get_account.assert_awaited_once()
        client.async_close.assert_awaited()
