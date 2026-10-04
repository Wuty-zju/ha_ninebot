"""SMS requests are fake only; production sessions and phone gateways are never used."""

import asyncio
import hashlib
import json
import os
import time
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ninebot import manager_for
from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.session import SessionManager, session_uid

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
PHONE = "19900000000"


@pytest.fixture
async def sms_manager(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    manager = manager_for(hass)
    client = AsyncMock()
    client.async_list_vehicles.return_value = []
    uid = "synthetic-business"

    def factory(path, http):
        async def consume(account, code):
            await hass.async_add_executor_job(
                (path / "tokens.json").write_text,
                json.dumps({"business_uid": uid, "access_token": "synthetic-sms-token"}),
            )

        if client.async_consume_login_code.side_effect is None:
            client.async_consume_login_code.side_effect = consume
        return client

    manager._factory = factory
    with (
        patch("custom_components.ninebot.async_setup_entry", return_value=True),
        patch("custom_components.ninebot.async_unload_entry", return_value=True),
    ):
        yield manager, client
    await manager.async_cleanup_sms(shutdown=True)


async def start(hass, **features):
    form = await hass.config_entries.flow.async_init("ninebot", context={"source": SOURCE_USER})
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": PHONE, "login_method": "sms", **features}
    )


async def test_two_steps_do_not_commit_or_hold_mutex_until_code(hass, sms_manager):
    manager, client = sms_manager
    form = await start(hass, debug_mode=True)
    assert form["step_id"] == "sms_code"
    assert not manager._lock.locked() and not manager._live_transactions
    assert not hass.config_entries.async_entries("ninebot")
    challenge = next(iter(manager._sms.values()))
    assert challenge.path.exists() and not (challenge.path / "tokens.json").exists()
    assert not client.async_consume_login_code.await_count
    result = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": "123456"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options["debug_mode"] is True
    assert "password" not in entry.data and "code" not in entry.data
    assert session_uid(manager.path(entry.data["session_key"])) == "synthetic-business"
    assert not manager._sms and not list(manager.root.glob(".candidate-*"))
    client.async_send_login_code.assert_awaited_once_with(PHONE)
    client.async_consume_login_code.assert_awaited_once_with(PHONE, "123456")
    assert not client.async_login.await_count


async def test_invalid_code_and_resend_cooldown_do_not_auto_send(hass, sms_manager):
    manager, client = sms_manager
    form = await start(hass)
    form = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": "wrong"})
    assert form["errors"] == {"code": "invalid_code"}
    assert not client.async_consume_login_code.await_count
    form = await hass.config_entries.flow.async_configure(form["flow_id"], {"resend": True})
    assert form["errors"] == {"base": "sms_cooldown"}
    assert client.async_send_login_code.await_count == 1
    manager._sms_attempts[hashlib.sha256(PHONE.encode()).hexdigest()] -= 61
    form = await hass.config_entries.flow.async_configure(form["flow_id"], {"resend": True})
    assert form["errors"] == {} and client.async_send_login_code.await_count == 2
    client.async_consume_login_code.side_effect = NinebotError(ErrorKind.AUTH)
    form = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": "000000"})
    assert form["errors"] == {"base": "invalid_code"}
    assert client.async_send_login_code.await_count == 2
    hass.config_entries.flow.async_abort(form["flow_id"])
    await hass.async_block_till_done()
    assert not manager._sms and not list(manager.root.glob(".candidate-*"))


@pytest.mark.parametrize(
    "source,reason",
    [(SOURCE_REAUTH, "reauth_successful"), (SOURCE_RECONFIGURE, "reconfigure_successful")],
)
async def test_sms_uses_existing_same_account_replacement(hass, entry, sms_manager, source, reason):
    manager, client = sms_manager
    old = manager.path(entry.data["session_key"])
    old.mkdir(parents=True)
    (old / "tokens.json").write_text(
        json.dumps({"business_uid": "synthetic-business", "access_token": "old"})
    )
    form = await hass.config_entries.flow.async_init(
        "ninebot",
        context={"source": source, "entry_id": entry.entry_id},
        data=entry.data if source == SOURCE_REAUTH else None,
    )
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": PHONE, "login_method": "sms"}
    )
    assert json.loads((old / "tokens.json").read_text())["access_token"] == "old"
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": "123456"})
    assert result["reason"] == reason
    assert json.loads((old / "tokens.json").read_text())["access_token"] == "synthetic-sms-token"
    assert entry.unique_id == "synthetic-business"
    assert not list(manager.root.glob(".candidate-*"))


async def test_wrong_sms_identity_never_changes_current_session(hass, entry, sms_manager):
    manager, client = sms_manager
    old_data = dict(entry.data)
    old = manager.path(entry.data["session_key"])
    old.mkdir(parents=True)
    before = json.dumps({"business_uid": "synthetic-business", "access_token": "old"})
    (old / "tokens.json").write_text(before)
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": PHONE, "login_method": "sms"}
    )
    challenge = next(iter(manager._sms.values()))

    async def wrong(account, code):
        (challenge.path / "tokens.json").write_text(json.dumps({"business_uid": "other"}))

    client.async_consume_login_code.side_effect = wrong
    result = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": "123456"})
    assert result["reason"] == "wrong_account"
    assert (old / "tokens.json").read_text() == before and dict(entry.data) == old_data
    assert not list(manager.root.glob(".candidate-*"))


async def test_abort_during_consume_cleans_candidate_and_closes_client(hass, sms_manager):
    manager, client = sms_manager
    form = await start(hass)
    entered, wait = asyncio.Event(), asyncio.Event()

    async def consume(account, code):
        entered.set()
        await wait.wait()

    client.async_consume_login_code.side_effect = consume
    task = asyncio.create_task(
        hass.config_entries.flow.async_configure(form["flow_id"], {"code": "123456"})
    )
    await entered.wait()
    hass.config_entries.flow.async_abort(form["flow_id"])
    with pytest.raises(asyncio.CancelledError):
        await task
    await hass.async_block_till_done()
    assert not manager._sms and not list(manager.root.glob(".candidate-*"))
    assert client.async_close.await_count >= 2
    assert not hass.config_entries.async_entries("ninebot")


async def test_failed_send_cooldown_and_sms_only_crash_cleanup(tmp_path):
    client = AsyncMock()
    client.async_send_login_code.side_effect = NinebotError(ErrorKind.CONNECTION)
    async with aiohttp.ClientSession() as http:
        manager = SessionManager(tmp_path / "private", http, lambda path, session: client)
        with pytest.raises(NinebotError) as error:
            await manager.async_send_sms(PHONE)
        assert error.value.kind is ErrorKind.CONNECTION
        assert not list(manager.root.glob(".candidate-*"))
        with pytest.raises(NinebotError) as error:
            await manager.async_send_sms(PHONE)
        assert error.value.kind is ErrorKind.BUSY
        client.async_send_login_code.assert_awaited_once()
        stale = manager.root / ".candidate-sms-crash"
        normal = manager.root / ".candidate-password"
        stale.mkdir()
        normal.mkdir()
        os.utime(stale, (time.time() - 601, time.time() - 601))
        os.utime(normal, (time.time() - 601, time.time() - 601))
        await manager.async_cleanup_sms()
        assert not stale.exists() and normal.exists()


async def test_password_required_and_phone_validation_before_send(hass, sms_manager):
    manager, client = sms_manager
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_USER}, data={"account": "fake"}
    )
    assert form["errors"] == {"password": "password_required"}
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": "fake", "login_method": "sms"}
    )
    assert form["errors"] == {"base": "invalid_account"}
    assert not client.async_send_login_code.await_count
    hass.config_entries.flow.async_abort(form["flow_id"])


async def test_idle_flow_timeout_collects_private_sms_directory(hass, sms_manager, freezer):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    manager, client = sms_manager
    form = await start(hass)
    directory = next(iter(manager._sms))
    freezer.tick(timedelta(seconds=601))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert not any(
        flow["flow_id"] == form["flow_id"] for flow in hass.config_entries.flow.async_progress()
    )
    assert not directory.exists() and not manager._sms
    client.async_send_login_code.assert_awaited_once()
