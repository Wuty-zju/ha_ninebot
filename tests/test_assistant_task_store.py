"""Native private Store acknowledgement and serialized confirmation with synthetic HA."""

import asyncio
import copy
from datetime import date, timedelta
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
REAL_WRITE, REAL_LOAD, REAL_REMOVE = Store._async_write_data, Store._async_load, Store.async_remove


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
        patch.object(Store, "async_remove", REAL_REMOVE),
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
                "enable_assistant_tasks": True,
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


async def store_exists(store):
    return await store.hass.async_add_executor_job(Path(store._store.path).exists)


async def test_closed_owner_preserves_pending_for_a_new_owner(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    await s.async_close()
    with pytest.raises(TaskStoreUnavailable, match="closed"):
        await s.async_prepare(pending.task_id, c)
    reopened = ApprovalStore(s.hass, s.entry_id, s.account_key)
    prepared, nonce = await reopened.async_prepare(pending.task_id, c)
    assert prepared.plan == p and nonce
    await reopened.async_close(remove=True)
    assert not await store_exists(s)


async def test_removal_drains_cancelled_close_and_revokes_the_inflight_writer(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    original = s._store.async_save
    written, release = asyncio.Event(), asyncio.Event()

    async def save(data):
        await original(data)
        written.set()
        await release.wait()

    with patch.object(s._store, "async_save", new=save):
        writer = asyncio.create_task(s.async_request(p, c))
        await asyncio.wait_for(written.wait(), 10)
        closing = asyncio.create_task(s.async_close(remove=True))
        try:
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
        finally:
            release.set()
        with pytest.raises(AssistantError, match="^not_authorized$"):
            await writer
        with pytest.raises(asyncio.CancelledError):
            await closing
    assert not await store_exists(s)


async def test_silent_remove_failure_leaves_the_old_owner_closed(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    with patch.object(s._store, "async_remove", new=AsyncMock()):
        with pytest.raises(TaskStoreUnavailable, match="acknowledged"):
            await s.async_close(remove=True)
    with pytest.raises(TaskStoreUnavailable, match="closed"):
        await s.async_prepare(pending.task_id, c)


async def seeded_request(hass, entry, task_store, *, context=None):
    from datetime import UTC, datetime

    from custom_components.ninebot.adapters import travel
    from custom_components.ninebot.assistant_tasks import async_request_details

    start = int(datetime(2026, 11, 6, 2, tzinfo=UTC).timestamp())
    month = travel(
        {
            "times": 1,
            "list": [
                {
                    "travel_id": "private-task-ride",
                    "start_time": start,
                    "end_time": start + 600,
                    "duration": 600,
                    "mileages": 1.2,
                }
            ],
        },
        "202611",
    )
    await task_store.co.statistics.archive.async_record_month(
        "SyntheticSN", month, dt_util.utcnow()
    )
    return await async_request_details(
        hass, context or task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
    )


async def test_request_owns_one_private_store_and_deduplicates_actual_digest(
    hass, entry, task_store
):
    import hashlib
    import json

    from custom_components.ninebot.assistant_tasks import async_request_details

    first = await seeded_request(hass, entry, task_store)
    store = entry.runtime_data.assistant_tasks
    assert store is not None and first["task_recorded"] is True
    assert first["task"]["state"] == "awaiting_approval"
    path = task_store.co.statistics.archive.path
    before = hashlib.sha256(path.read_bytes()).digest()
    second = await async_request_details(
        hass, task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
    )
    assert entry.runtime_data.assistant_tasks is store
    assert first["task"] == second["task"] and first["plan"] == second["plan"]
    assert hashlib.sha256(path.read_bytes()).digest() == before
    raw = json.dumps(first)
    for secret in ("nonce", "SyntheticSN", "private-task-ride", store.account_key):
        assert secret not in raw
    pending = (await store._read())[first["task"]["task_id"]]
    assert pending.nonce_hash is None and pending.approved_at is None
    _, nonce = await store.async_prepare(pending.task_id, task_store.context)
    await store.async_approve(pending.task_id, task_store.context, nonce, pending.plan.digest)
    repeated = await async_request_details(
        hass, task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
    )
    assert repeated["task"]["state"] == "approved" and repeated["approval_required"] is False


async def test_task_consent_is_separate_and_archive_import_barrier_revokes_pending(
    hass, entry, task_store
):
    from custom_components.ninebot.assistant_tasks import async_request_details, async_task_barrier

    first = await seeded_request(hass, entry, task_store)
    old = entry.runtime_data.assistant_tasks
    pending, nonce = await old.async_prepare(first["task"]["task_id"], task_store.context)
    async with async_task_barrier(hass, entry):
        assert not await store_exists(old)
        with pytest.raises(AssistantError, match="^not_authorized$"):
            await async_request_details(
                hass, task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
            )
    second = await async_request_details(
        hass, task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
    )
    new = entry.runtime_data.assistant_tasks
    assert new is not old and first["task"]["task_id"] != second["task"]["task_id"]
    with pytest.raises(AssistantError, match="^task_not_found$"):
        await new.async_approve(pending.task_id, task_store.context, nonce, pending.plan.digest)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "enable_assistant_tasks": False}
    )
    await hass.async_block_till_done()
    assert not await store_exists(new)
    with pytest.raises(AssistantError, match="^not_authorized$"):
        await async_request_details(
            hass, task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
        )


async def test_removing_account_keeps_history_but_not_task_authority(hass, entry, task_store):
    await seeded_request(hass, entry, task_store)
    store = entry.runtime_data.assistant_tasks
    history = task_store.co.statistics.archive.path
    assert history.exists() and await store_exists(store)
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert history.exists() and not await store_exists(store)


async def test_request_does_not_acknowledge_a_replaced_owner(hass, entry, task_store):
    from custom_components.ninebot.assistant_tasks import async_request_details

    await seeded_request(hass, entry, task_store)
    old = entry.runtime_data.assistant_tasks
    original = old.async_request

    async def replaced(*args, **kwargs):
        result = await original(*args, **kwargs)
        entry.runtime_data.assistant_tasks = None
        return result

    try:
        with (
            patch.object(old, "async_request", new=replaced),
            pytest.raises(AssistantError, match="^not_authorized$"),
        ):
            await async_request_details(
                hass, task_store.context, task_store.plan.vehicle_ref, "2026-11-06", "2026-11-06"
            )
    finally:
        entry.runtime_data.assistant_tasks = old


@pytest.fixture
async def review_client(hass, task_store, hass_ws_client):
    from custom_components.ninebot.assistant_review import async_register

    async_register(hass)
    try:
        client = await hass_ws_client(hass)
    except TypeError:
        client = await hass_ws_client()
    return client


async def review(client, vehicle, identity, operation, **args):
    await client.send_json(
        {
            "id": identity,
            "type": "ninebot/assistant/review",
            "vehicle_ref": vehicle,
            "operation": operation,
            **args,
        }
    )
    return await asyncio.wait_for(client.receive_json(), 10)


async def test_real_websocket_review_uses_caller_and_never_registers_approval_services(
    hass, entry, task_store, review_client, hass_user
):
    request = await seeded_request(hass, entry, task_store, context=Context(user_id=hass_user.id))
    device, client = task_store.plan.vehicle_ref, review_client
    task_id = request["task"]["task_id"]
    listed = await review(client, device, 1, "list")
    assert listed["success"] and listed["result"]["tasks"][0]["task_id"] == task_id
    assert "token" not in str(listed)
    prepared = await review(client, device, 2, "prepare", task_id=task_id)
    assert prepared["success"]
    response = prepared["result"]
    assert response["token"] and response["task"]["ride_ids"] == ["private-task-ride"]
    assert response["task"]["maximum_detail_attempts"] == 1
    approved = await review(
        client,
        device,
        3,
        "approve",
        task_id=task_id,
        token=response["token"],
        plan_hash=response["task"]["plan_hash"],
    )
    assert approved["success"] and approved["result"]["task"]["state"] == "approved"
    assert "token" not in str(approved)
    replay = await review(
        client,
        device,
        4,
        "approve",
        task_id=task_id,
        token=response["token"],
        plan_hash=response["task"]["plan_hash"],
    )
    assert replay["error"]["code"] == "approval_used"
    assert not hass.services.has_service("ninebot", "prepare_task_confirmation")
    assert not hass.services.has_service("ninebot", "approve_task")


async def test_real_websocket_rejects_other_requester_and_mismatched_fields(
    hass, entry, task_store, review_client
):
    request = await seeded_request(hass, entry, task_store)
    device, client = task_store.plan.vehicle_ref, review_client
    hidden = await review(client, device, 1, "list")
    assert hidden["success"] and hidden["result"]["tasks"] == []
    denied = await review(client, device, 2, "prepare", task_id=request["task"]["task_id"])
    assert denied["error"]["code"] == "not_authorized"
    malformed = await review(client, device, 3, "list", task_id=request["task"]["task_id"])
    assert malformed["error"]["code"] == "invalid_arguments"
    with patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=None)):
        removed = await review(client, device, 4, "list")
    assert removed["error"]["code"] == "not_authorized"


async def test_websocket_nonce_rotation_expiry_and_task_disable(
    hass, entry, task_store, review_client, hass_user
):
    request = await seeded_request(hass, entry, task_store, context=Context(user_id=hass_user.id))
    device, client, task_id = task_store.plan.vehicle_ref, review_client, request["task"]["task_id"]
    first = (await review(client, device, 1, "prepare", task_id=task_id))["result"]
    second = (await review(client, device, 2, "prepare", task_id=task_id))["result"]
    assert first["token"] != second["token"]
    stale = await review(
        client,
        device,
        3,
        "approve",
        task_id=task_id,
        token=first["token"],
        plan_hash=first["task"]["plan_hash"],
    )
    assert stale["error"]["code"] == "not_authorized"
    # Advance only this ticket's deadline without running unrelated HA timers.
    original = dt_util.utcnow()
    with patch(
        "custom_components.ninebot.assistant_task_store.dt_util.utcnow",
        return_value=original + timedelta(minutes=11),
    ):
        expired = await review(client, device, 4, "prepare", task_id=task_id)
    assert expired["error"]["code"] == "approval_expired"
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "enable_assistant_tasks": False}
    )
    await hass.async_block_till_done()
    disabled = await review(client, device, 5, "list")
    assert disabled["error"]["code"] == "not_authorized"
