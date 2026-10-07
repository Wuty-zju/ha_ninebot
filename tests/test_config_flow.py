import asyncio
import json
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ninebot.config_flow import ERRORS
from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.session import Candidate, session_uid

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def flow_manager(hass, tmp_path):
    from custom_components.ninebot import manager_for

    hass.config.config_dir = str(tmp_path)
    manager = manager_for(hass)

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
    assert entry.title == "fake-account"
    assert entry.options["enable_coordinates"] is True


async def test_account_title_metadata_and_explicit_location_off(hass, flow_manager):
    from dataclasses import replace

    from custom_components.ninebot.account import AccountDisplay

    prepare = flow_manager.async_prepare.side_effect

    async def with_display(account, password):
        return replace(await prepare(account, password), display=AccountDisplay("Rider", "bj"))

    flow_manager.async_prepare.side_effect = with_display
    result = await hass.config_entries.flow.async_init(
        "ninebot",
        context={"source": SOURCE_USER},
        data={
            "account": "test-account",
            "password": "synthetic-password",
            "enable_coordinates": False,
        },
    )
    entry = result["result"]
    assert entry.title == "Rider：test-account[bj]"
    assert entry.data["account_display"] == {"username": "Rider", "region": "bj"}
    assert entry.options["enable_coordinates"] is False


async def test_reauth_keeps_custom_entry_title(hass, entry, flow_manager):
    hass.config_entries.async_update_entry(entry, title="My vehicles")
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.title == "My vehicles"


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


async def test_failed_unload_never_revalidates_or_replaces_private_session(
    hass, entry, flow_manager
):
    from homeassistant.config_entries import ConfigEntryState

    assert await hass.config_entries.async_setup(entry.entry_id)
    with patch("custom_components.ninebot.async_unload_entry", return_value=False):
        assert not await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.FAILED_UNLOAD
    flow_manager.async_prepare.reset_mock()
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
    )
    assert result["errors"] == {"base": "busy"}
    flow_manager.async_prepare.assert_not_awaited()
    assert not flow_manager.root.exists()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_rollback_unload_retains_journal_and_reports_recovery(
    hass, entry, flow_manager, cancelled
):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.ninebot.session import SessionManager

    path = flow_manager.path(entry.data["session_key"])
    path.mkdir(parents=True)
    before = json.dumps({"business_uid": "synthetic-business", "access_token": "prior"})
    (path / "tokens.json").write_text(before)
    form = await hass.config_entries.flow.async_init(
        "ninebot", context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    with (
        patch.object(
            flow_manager,
            "async_finalize",
            side_effect=asyncio.CancelledError() if cancelled else OSError("synthetic-disk"),
        ),
        patch.object(hass.config_entries, "async_unload", return_value=False),
    ):
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await hass.config_entries.flow.async_configure(
                    form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
                )
        else:
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], {"account": "fake-account", "password": "synthetic-password"}
            )
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "session_recovery_pending"
    assert ir.async_get(hass).async_get_issue("ninebot", f"session_recovery_{entry.entry_id}")
    assert await flow_manager.async_is_pending(entry.data["session_key"])
    assert json.loads((path / "tokens.json").read_text())["access_token"] == "synthetic-new-token"
    # A fresh manager represents restart, with no live flow owning the journal.
    recovery = SessionManager(flow_manager.root, None)
    await recovery.async_recover(entry.data["session_key"])
    assert (path / "tokens.json").read_text() == before
    assert not await recovery.async_is_pending(entry.data["session_key"])


async def test_two_account_entries_keep_metadata_and_token_directories_separate(hass, flow_manager):
    from custom_components.ninebot.account import AccountDisplay

    async def prepare(account, password):
        directory = await hass.async_add_executor_job(flow_manager._candidate_dir)
        uid = f"business-{account}"
        await hass.async_add_executor_job(
            (directory / "tokens.json").write_text,
            json.dumps({"business_uid": uid, "access_token": "synthetic"}),
        )
        return Candidate(directory, uid, AccountDisplay(f"Rider-{account}", "bj"))

    flow_manager.async_prepare.side_effect = prepare
    results = []
    for account in ("one", "two"):
        result = await hass.config_entries.flow.async_init(
            "ninebot",
            context={"source": SOURCE_USER},
            data={"account": account, "password": "synthetic"},
        )
        results.append(result["result"])
    first, second = results
    assert first.unique_id != second.unique_id
    assert first.data["session_key"] != second.data["session_key"]
    assert first.title == "Rider-one：one[bj]" and second.title == "Rider-two：two[bj]"
    for entry in results:
        assert session_uid(flow_manager.path(entry.data["session_key"])) == entry.unique_id
