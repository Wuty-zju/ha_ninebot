import asyncio
import json
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ninebot.config_flow import ERRORS
from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.session import Candidate, SessionManager, session_uid

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def flow_manager(hass, tmp_path):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    manager = SessionManager(tmp_path / "private", async_get_clientsession(hass))

    async def prepare(account, password):
        directory = await hass.async_add_executor_job(manager._candidate_dir)
        await hass.async_add_executor_job(
            (directory / "tokens.json").write_text,
            json.dumps(
                {"business_uid": "synthetic-business", "access_token": "synthetic-new-token"}
            ),
        )
        return Candidate(directory, "synthetic-business")

    with (
        patch(
            "custom_components.ninebot.config_flow.NinebotConfigFlow._manager", return_value=manager
        ),
        patch.object(manager, "async_prepare", side_effect=prepare),
        patch("custom_components.ninebot.async_setup_entry", return_value=True),
        patch("custom_components.ninebot.async_unload_entry", return_value=True),
    ):
        yield manager


async def test_user_success_no_password_stored(hass, flow_manager):
    form = await hass.config_entries.flow.async_init("ninebot", context={"source": SOURCE_USER})
    assert form["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": " fake-account ", "password": "synthetic-password"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == "synthetic-business"
    assert entry.data["account"] == "fake-account"
    assert "password" not in entry.data
    assert session_uid(flow_manager.path(entry.data["session_key"])) == "synthetic-business"
    assert not list(flow_manager.root.glob(".candidate-*"))


@pytest.mark.parametrize("kind", list(ErrorKind))
async def test_error_categories_keep_form_without_commit(hass, flow_manager, kind):
    flow_manager.async_prepare.side_effect = NinebotError(kind)
    result = await hass.config_entries.flow.async_init(
        "ninebot",
        context={"source": SOURCE_USER},
        data={"account": "fake", "password": "synthetic-password"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == ERRORS[kind]
    assert not flow_manager.root.exists()


async def test_duplicate_validated_candidate_never_replaces_old_session(hass, entry, flow_manager):
    path = flow_manager.path(entry.data["session_key"])
    path.mkdir(parents=True)
    before = json.dumps({"business_uid": "synthetic-business", "access_token": "prior"})
    (path / "tokens.json").write_text(before)
    result = await hass.config_entries.flow.async_init(
        "ninebot",
        context={"source": SOURCE_USER},
        data={"account": "fake-account", "password": "synthetic-password"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert (path / "tokens.json").read_text() == before
    assert not list(flow_manager.root.glob(".candidate-*"))


async def test_wrong_account_reauth_discards_candidate(hass, entry, flow_manager):
    async def wrong(account, password):
        directory = flow_manager._candidate_dir()
        return Candidate(directory, "other-business")

    flow_manager.async_prepare.side_effect = wrong
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
    )
    assert result["reason"] == "wrong_account"
    assert not list(flow_manager.root.glob(".candidate-*"))


@pytest.mark.parametrize(
    "source,reason",
    [(SOURCE_REAUTH, "reauth_successful"), (SOURCE_RECONFIGURE, "reconfigure_successful")],
)
async def test_same_account_reauth_and_reconfigure(hass, entry, flow_manager, source, reason):
    form = await hass.config_entries.flow.async_init(
        "ninebot",
        context={"source": source, "entry_id": entry.entry_id},
        data=entry.data if source == SOURCE_REAUTH else None,
    )
    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
        )
    assert result["reason"] == reason
    reload.assert_awaited_once_with(entry.entry_id)
    assert session_uid(flow_manager.path(entry.data["session_key"])) == "synthetic-business"
    assert "password" not in entry.data


async def test_storage_failure_rolls_back_session_and_config(hass, entry, flow_manager):
    path = flow_manager.path(entry.data["session_key"])
    path.mkdir(parents=True)
    before = json.dumps({"business_uid": "synthetic-business", "access_token": "prior"})
    (path / "tokens.json").write_text(before)
    old_data = dict(entry.data)
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    with patch.object(
        flow_manager, "async_finalize", side_effect=OSError("synthetic-disk-failure")
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "session_update_failed"
    assert (path / "tokens.json").read_text() == before
    assert dict(entry.data) == old_data


async def test_options_defaults_are_safe(hass, entry, flow_manager):
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "poll_interval": 120,
            "enable_estimation": False,
            "enable_coordinates": False,
            "enable_controls": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["enable_controls"] is False


@pytest.mark.parametrize("cancelled", [False, True])
async def test_reload_failure_or_cancel_restores_prior_session(
    hass, entry, flow_manager, cancelled
):
    path = flow_manager.path(entry.data["session_key"])
    path.mkdir(parents=True)
    before = json.dumps({"business_uid": "synthetic-business", "access_token": "prior"})
    (path / "tokens.json").write_text(before)
    old_data = dict(entry.data)
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    attempts = []

    async def reload(entry_id):
        await flow_manager.async_recover(entry.data["session_key"])
        attempts.append((path / "tokens.json").read_text())
        if len(attempts) == 1:
            assert json.loads(attempts[0])["access_token"] == "synthetic-new-token"
            if cancelled:
                raise asyncio.CancelledError
            return False
        assert attempts[-1] == before
        return True

    with patch.object(hass.config_entries, "async_reload", side_effect=reload):
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await hass.config_entries.flow.async_configure(
                    form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
                )
        else:
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
            )
            assert result["errors"] == {"base": "cannot_connect"}
    assert len(attempts) == 2
    assert dict(entry.data) == old_data
    assert (path / "tokens.json").read_text() == before
    assert not list(flow_manager.root.glob(".*"))


async def test_cancellation_after_finalize_keeps_accepted_metadata(hass, entry, flow_manager):
    path = flow_manager.path(entry.data["session_key"])
    path.mkdir(parents=True)
    (path / "tokens.json").write_text(
        json.dumps({"business_uid": "synthetic-business", "access_token": "prior"})
    )
    original = flow_manager.async_finalize

    async def finalize(key):
        await original(key)
        raise asyncio.CancelledError

    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    with (
        patch.object(flow_manager, "async_finalize", side_effect=finalize),
        patch.object(hass.config_entries, "async_reload", return_value=True) as reload,
    ):
        with pytest.raises(asyncio.CancelledError):
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {"account": "updated-alias", "password": "synthetic-password"}
            )
    assert entry.data["account"] == "updated-alias"
    assert json.loads((path / "tokens.json").read_text())["access_token"] == "synthetic-new-token"
    reload.assert_awaited_once()
    assert not list(flow_manager.root.glob(".*"))


async def test_loaded_runtime_must_unload_before_replacing_session(hass, entry, flow_manager):
    assert await hass.config_entries.async_setup(entry.entry_id)
    path = flow_manager.path(entry.data["session_key"])
    path.mkdir(parents=True)
    before = json.dumps({"business_uid": "synthetic-business", "access_token": "prior"})
    (path / "tokens.json").write_text(before)
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    with patch.object(hass.config_entries, "async_unload", return_value=False):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
        )
    assert result["errors"] == {"base": "busy"}
    assert (path / "tokens.json").read_text() == before
    assert not list(flow_manager.root.glob(".*"))
