"""Native private Store acknowledgement and serialized confirmation with synthetic HA."""

import asyncio
import copy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.ninebot.assistant_approval import DetailPlan
from custom_components.ninebot.assistant_policy import AssistantError
from custom_components.ninebot.assistant_task_store import (
    ApprovalStore,
    TaskStoreUnavailable,
    restore_ticket,
    ticket_data,
)
from custom_components.ninebot.preservation import DetailScope

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
REAL_WRITE, REAL_LOAD = Store._async_write_data, Store._async_load


@pytest.fixture
async def task_store(hass, entry, app_client, tmp_path):
    hass.config.config_dir = str(tmp_path)
    principal = SimpleNamespace(
        is_active=True,
        is_admin=True,
        permissions=SimpleNamespace(access_all_entities=Mock(return_value=True)),
    )
    with (
        patch.object(Store, "_async_write_data", REAL_WRITE),
        patch.object(Store, "_async_load", REAL_LOAD),
        patch("custom_components.ninebot.async_update_assistant_api", new=AsyncMock()),
        patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"),
        patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=principal)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        co = entry.runtime_data.coordinator
        co.realtime.stop()
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                "enable_assistant": True,
                "assistant_vehicles": ["SyntheticSN"],
            },
        )
        await hass.async_block_till_done()
        device = next(
            d.id
            for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if ("ninebot", "SyntheticSN") in d.identifiers
        )
        plan = DetailPlan(
            "synthetic-admin",
            co.statistics.archive.owner,
            device,
            "SyntheticSN",
            date(2026, 11, 5),
            date(2026, 11, 6),
            DetailScope((("synthetic-ride", "b" * 64),)),
            2,
            dt_util.utcnow(),
        )
        store = ApprovalStore(hass, entry.entry_id, plan.account_key)
        app_client.reset_mock()
        yield SimpleNamespace(store=store, plan=plan, context=Context(user_id=plan.user_id), co=co)
        for method in (
            app_client.async_list_vehicles,
            app_client.async_get_status,
            app_client.async_get_battery,
            app_client.async_get_travel,
            app_client.async_get_trip_detail,
            app_client.async_control,
        ):
            method.assert_not_awaited()


async def test_pending_request_has_no_nonce_and_reopen_rejects_replay(hass, task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    assert pending.nonce_hash is None
    assert (await s.async_request(p, c)).task_id == pending.task_id
    prepared, nonce = await s.async_prepare(pending.task_id, c)
    assert nonce not in await hass.async_add_executor_job(Path(s._store.path).read_text)
    approved = await s.async_approve(prepared.task_id, c, nonce, p.digest)
    assert approved.approved_at and approved.nonce_hash == ""
    reopened = ApprovalStore(hass, s.entry_id, s.account_key)
    with pytest.raises(AssistantError, match="^approval_used$"):
        await reopened.async_approve(prepared.task_id, c, nonce, p.digest)


async def test_competing_approvals_consume_one_confirmation(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    _, nonce = await s.async_prepare(pending.task_id, c)
    outcomes = await asyncio.gather(
        s.async_approve(pending.task_id, c, nonce, p.digest),
        s.async_approve(pending.task_id, c, nonce, p.digest),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    errors = [item for item in outcomes if isinstance(item, AssistantError)]
    assert len(errors) == 1 and errors[0].code == "approval_used"


async def test_prepare_rotates_nonce_without_reissuing_the_plan(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    first, old_nonce = await s.async_prepare(pending.task_id, c)
    second, new_nonce = await s.async_prepare(pending.task_id, c)
    assert first.plan == second.plan and old_nonce != new_nonce
    with pytest.raises(AssistantError, match="^not_authorized$"):
        await s.async_approve(pending.task_id, c, old_nonce, p.digest)
    assert (await s.async_approve(pending.task_id, c, new_nonce, p.digest)).approved_at


async def test_silent_store_failure_never_acknowledges_a_request(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    with patch.object(s._store, "async_save", new=AsyncMock()):
        with pytest.raises(TaskStoreUnavailable, match="acknowledged"):
            await s.async_request(p, c)


async def test_cancelled_durable_approval_keeps_lock_until_write_finishes(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    _, nonce = await s.async_prepare(pending.task_id, c)
    original = s._store.async_save
    written, release = asyncio.Event(), asyncio.Event()

    async def save(data):
        await original(data)
        written.set()
        await release.wait()

    with patch.object(s._store, "async_save", new=save):
        first = asyncio.create_task(s.async_approve(pending.task_id, c, nonce, p.digest))
        await written.wait()
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()
        following = asyncio.create_task(s.async_approve(pending.task_id, c, nonce, p.digest))
        await asyncio.sleep(0)
        assert not following.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(AssistantError, match="^approval_used$"):
            await following


async def test_private_store_is_not_read_without_admin_identity(task_store):
    with patch.object(task_store.store, "_read", new=AsyncMock()) as read:
        with pytest.raises(Unauthorized):
            await task_store.store.async_prepare("a" * 32, None)
        read.assert_not_awaited()


async def test_changed_contract_or_unknown_fields_fail_closed(task_store):
    pending = await task_store.store.async_request(task_store.plan, task_store.context)
    raw = ticket_data(pending)
    assert restore_ticket(raw) == pending
    for changed in (
        {**raw, "plan_hash": "0" * 64},
        {**raw, "unknown": True},
        {**raw, "approved_at": "2026-11-07T00:00:00"},
    ):
        with pytest.raises(TaskStoreUnavailable):
            restore_ticket(copy.deepcopy(changed))
