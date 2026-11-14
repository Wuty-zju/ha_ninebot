"""Native private Store acknowledgement and serialized confirmation with synthetic HA."""

import asyncio
import copy
import json
import os
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
from custom_components.ninebot.assistant_execution import AssistantExecution
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
REAL_EXECUTION_STEP = AssistantExecution.async_step


@pytest.fixture
async def task_store(hass, entry, app_client, tmp_path, request):
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
        patch.object(AssistantExecution, "async_step", new=AsyncMock(return_value=False)),
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
        yield SimpleNamespace(
            store=store, plan=plan, context=Context(user_id=plan.user_id), co=co, client=app_client
        )
        for method in (
            app_client.async_list_vehicles,
            app_client.async_get_status,
            app_client.async_get_battery,
            app_client.async_get_travel,
            app_client.async_get_trip_detail,
            app_client.async_control,
        ):
            if method is app_client.async_get_trip_detail and getattr(request, "param", False):
                continue
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


@pytest.mark.parametrize("replace_driver", [False, True])
async def test_closed_driver_does_not_reattach_after_delayed_restore(
    hass, entry, task_store, replace_driver
):
    from custom_components.ninebot.assistant_execution import driver_for

    store, plan, context = task_store.store, task_store.plan, task_store.context
    await approve_task(store, await store.async_request(plan, context), context)
    entry.runtime_data.assistant_tasks = store
    driver = driver_for(hass, entry)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.async_execution_tickets

    async def delayed_read():
        tickets = await original()
        entered.set()
        await release.wait()
        return tickets

    with patch.object(store, "async_execution_tickets", side_effect=delayed_read):
        restoring = asyncio.create_task(driver.async_restore())
        await entered.wait()
        task_store.co.preservation.detach_assistant()
        replacement = driver_for(hass, entry) if replace_driver else None
        release.set()
        await restoring

    assert driver.closed and not driver.tickets and not driver.contexts
    assert task_store.co.preservation._assistant is replacement


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
    hass, entry, task_store, review_client, hass_admin_user
):
    request = await seeded_request(
        hass, entry, task_store, context=Context(user_id=hass_admin_user.id)
    )
    device, client = task_store.plan.vehicle_ref, review_client
    task_id = request["task"]["task_id"]
    listed = await review(client, device, 1, "list")
    assert listed["success"] and listed["result"]["tasks"][0]["task_id"] == task_id
    assert "token" not in str(listed)
    prepared = await review(client, device, 2, "prepare", task_id=task_id)
    assert prepared["success"]
    response = prepared["result"]
    assert response["token"] and response["task"]["ride_ids"] == ["private-task-ride"]
    assert response["task"]["maximum_detail_attempts"] == 2
    approved = await review(
        client,
        device,
        3,
        "approve",
        task_id=task_id,
        token=response["token"],
        plan_hash=response["task"]["plan_hash"],
    )
    assert approved["success"] and approved["result"]["task"]["state"] == "queued"
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
    hass, entry, task_store, review_client, hass_admin_user
):
    request = await seeded_request(
        hass, entry, task_store, context=Context(user_id=hass_admin_user.id)
    )
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


async def test_native_task_query_application_and_metadata_remain_local(hass, entry, task_store):
    import json

    from custom_components.ninebot.assistant_review import async_register
    from custom_components.ninebot.assistant_task_queries import async_task_query

    async_register(hass)
    await seeded_request(hass, entry, task_store)
    args = {
        "vehicle_ref": task_store.plan.vehicle_ref,
        "start_date": "2026-11-06",
        "end_date": "2026-11-06",
    }
    preview, error = await async_task_query(hass, "preview_detail_sync", args, task_store.context)
    assert not error and preview["data"]["task_created"] is False
    request, error = await async_task_query(
        hass, "request_detail_sync_approval", args, task_store.context
    )
    assert not error and request["data"]["task_recorded"] is True
    task_id = request["data"]["task"]["task_id"]
    for key, query in (
        ("get_tasks", {"vehicle_ref": args["vehicle_ref"]}),
        ("get_task", {"vehicle_ref": args["vehicle_ref"], "task_id": task_id}),
        ("task_vehicles", {}),
    ):
        result, error = await async_task_query(hass, key, query, task_store.context)
        assert not error and result["evidence"]["cloud_calls"] == 0
        output = json.dumps(result)
        for secret in (
            "nonce",
            "token",
            "private-task-ride",
            "SyntheticSN",
            task_store.plan.account_key,
        ):
            assert secret not in output
    for key, query in (
        ("approve_task", {}),
        ("request_detail_sync_approval", {**args, "confirmed": True}),
        ("get_task", {"vehicle_ref": args["vehicle_ref"], "task_id": "bad"}),
    ):
        result, error = await async_task_query(hass, key, query, task_store.context)
        assert error and result["error"]["code"] == "invalid_arguments"


async def test_task_vehicle_enumeration_filters_before_registry_and_scope(hass, entry, task_store):
    from custom_components.ninebot.assistant_review import async_register
    from custom_components.ninebot.assistant_task_queries import async_task_query

    async_register(hass)
    other = SimpleNamespace(options={"enable_assistant": True, "enable_assistant_tasks": False})
    lookup = dr.async_entries_for_config_entry
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[entry, other]),
        patch(
            "custom_components.ninebot.assistant_queries.dr.async_entries_for_config_entry",
            wraps=lookup,
        ) as read,
    ):
        result, error = await async_task_query(hass, "task_vehicles", {}, task_store.context)
    assert not error and len(result["data"]["vehicles"]) == 1
    assert result["evidence"]["capabilities_basis"] == "local_cache_and_archive_readiness"
    assert all(call.args[1] == entry.entry_id for call in read.call_args_list)


async def test_task_read_rechecks_owner_after_awaits(hass, entry, task_store):
    from custom_components.ninebot.assistant_review import async_register
    from custom_components.ninebot.assistant_task_queries import async_task_query

    async_register(hass)
    await seeded_request(hass, entry, task_store)
    old = entry.runtime_data.assistant_tasks
    original = old.async_list

    async def replaced(*args):
        result = await original(*args)
        entry.runtime_data.assistant_tasks = None
        return result

    try:
        with patch.object(old, "async_list", new=replaced):
            result, error = await async_task_query(
                hass, "get_tasks", {"vehicle_ref": task_store.plan.vehicle_ref}, task_store.context
            )
        assert error and result["error"]["code"] == "not_authorized"
    finally:
        entry.runtime_data.assistant_tasks = old


async def test_task_collection_does_not_inherit_read_only_consent(hass, entry, task_store):
    from custom_components.ninebot.assistant_review import async_register
    from custom_components.ninebot.assistant_task_queries import async_task_query

    async_register(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "enable_assistant_tasks": False}
    )
    await hass.async_block_till_done()
    result, error = await async_task_query(hass, "task_vehicles", {}, task_store.context)
    assert error and result["error"]["code"] == "not_authorized"


async def test_backup_copy_has_plans_but_no_approval_or_nonce(hass, task_store):
    from custom_components.ninebot.backup import async_post_backup, async_pre_backup

    s, p, c = task_store.store, task_store.plan, task_store.context
    task_store.co.config_entry.runtime_data.assistant_tasks = s
    pending = await s.async_request(p, c)
    _, nonce = await s.async_prepare(pending.task_id, c)
    approved = await s.async_approve(pending.task_id, c, nonce, p.digest)
    before = await hass.async_add_executor_job(Path(s._store.path).read_bytes)
    await async_pre_backup(hass)
    copied = await hass.async_add_executor_job(Path(s._store.path).read_bytes)
    restored = restore_ticket(json.loads(copied)["data"]["tickets"][pending.task_id])
    assert restored.plan == p and restored.approved_at is None and restored.nonce_hash is None
    assert not restored.execution_valid(dt_util.utcnow())
    with pytest.raises(AssistantError, match="^not_authorized$"):
        restored.approve(p.user_id, nonce, p.digest, dt_util.utcnow())
    with pytest.raises(AssistantError, match="^not_authorized$"):
        await s.async_prepare(pending.task_id, c)
    await async_post_backup(hass)
    assert await hass.async_add_executor_job(Path(s._store.path).read_bytes) == before
    assert await s.async_get(pending.task_id, c) == approved
    # A normal reopen can use its durable ticket; an actual backup copy cannot.
    reopened = ApprovalStore(hass, s.entry_id, s.account_key)
    assert (await reopened.async_get(pending.task_id, c)).execution_valid(dt_util.utcnow())
    await hass.async_add_executor_job(Path(s._store.path).write_bytes, copied)
    assert not (await reopened.async_get(pending.task_id, c)).execution_valid(dt_util.utcnow())


async def test_backup_also_removes_prepared_nonce_from_dormant_owner(hass, task_store):
    from custom_components.ninebot.backup import async_post_backup, async_pre_backup

    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    prepared, nonce = await s.async_prepare(pending.task_id, c)
    assert task_store.co.config_entry.runtime_data.assistant_tasks is None
    await async_pre_backup(hass)
    raw = await hass.async_add_executor_job(Path(s._store.path).read_text)
    assert prepared.nonce_hash not in raw and nonce not in raw
    await async_post_backup(hass)
    assert await s.async_get(pending.task_id, c) == prepared


async def test_backup_never_resurrects_removed_or_closed_task_owner(hass, task_store):
    from custom_components.ninebot.backup import async_post_backup, async_pre_backup

    s, p, c = task_store.store, task_store.plan, task_store.context
    task_store.co.config_entry.runtime_data.assistant_tasks = s
    pending = await s.async_request(p, c)
    await s.async_prepare(pending.task_id, c)
    await async_pre_backup(hass)
    await s.async_close(remove=True)
    await async_post_backup(hass)
    assert not await store_exists(s)


async def test_cancelled_backup_preparation_restores_after_durable_write(hass, task_store):
    from custom_components.ninebot.assistant_task_backup import _write
    from custom_components.ninebot.backup import async_pre_backup, backup_active

    s, p, c = task_store.store, task_store.plan, task_store.context
    task_store.co.config_entry.runtime_data.assistant_tasks = s
    pending = await s.async_request(p, c)
    prepared, _ = await s.async_prepare(pending.task_id, c)
    written, release = asyncio.Event(), asyncio.Event()

    async def write(hass, store, data):
        await _write(hass, store, data)
        written.set()
        await release.wait()

    with patch("custom_components.ninebot.assistant_task_backup._write", new=write):
        work = asyncio.create_task(async_pre_backup(hass))
        await written.wait()
        work.cancel()
        # Unwind uses the same durable helper; let the final restoration finish.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
    assert not backup_active(hass) and not task_store.co.statistics.archive.backup_paused
    assert await s.async_get(pending.task_id, c) == prepared


async def test_backup_restore_failure_still_thaws_archives(hass, task_store):
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ninebot.backup import async_post_backup, async_pre_backup, backup_active

    s, p, c = task_store.store, task_store.plan, task_store.context
    pending = await s.async_request(p, c)
    await s.async_prepare(pending.task_id, c)
    await async_pre_backup(hass)
    assert task_store.co.statistics.archive.backup_paused
    with patch(
        "custom_components.ninebot.assistant_task_backup._write",
        new=AsyncMock(side_effect=TaskStoreUnavailable("synthetic failure")),
    ):
        with pytest.raises(HomeAssistantError, match="could not be finalized"):
            await async_post_backup(hass)
    assert not backup_active(hass) and not task_store.co.statistics.archive.backup_paused


async def test_backup_preparation_does_not_replace_malformed_store(hass, task_store):
    from custom_components.ninebot.backup import async_pre_backup, backup_active

    s, p, c = task_store.store, task_store.plan, task_store.context
    await s.async_request(p, c)
    await hass.async_add_executor_job(Path(s._store.path).write_bytes, b"malformed synthetic store")
    with pytest.raises(TaskStoreUnavailable):
        await async_pre_backup(hass)
    assert not backup_active(hass) and not task_store.co.statistics.archive.backup_paused
    assert (
        await hass.async_add_executor_job(Path(s._store.path).read_bytes)
        == b"malformed synthetic store"
    )


async def approve_task(store, ticket, context):
    _, nonce = await store.async_prepare(ticket.task_id, context)
    return await store.async_approve(ticket.task_id, context, nonce, ticket.plan.digest)


async def test_cancel_is_durable_idempotent_and_does_not_dedup_a_new_request(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    first = await s.async_request(p, c)
    _, nonce = await s.async_prepare(first.task_id, c)
    cancelled = await s.async_cancel(first.task_id, c)
    assert cancelled.progress.state == "cancelled" and cancelled.nonce_hash is None
    reopened = ApprovalStore(s.hass, s.entry_id, s.account_key)
    assert await reopened.async_cancel(first.task_id, c) == cancelled
    with pytest.raises(AssistantError, match="^approval_used$"):
        await reopened.async_approve(first.task_id, c, nonce, p.digest)
    assert (await reopened.async_request(p, c)).task_id != first.task_id


async def test_checkpoint_reopen_and_cancel_rejects_late_runner_receipt(task_store):
    from custom_components.ninebot.assistant_approval import TaskProgress
    from custom_components.ninebot.assistant_task_views import ticket_view

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    running = await s.async_checkpoint(approved, TaskProgress("running", 0, 0, dt_util.utcnow()), c)
    reopened = ApprovalStore(s.hass, s.entry_id, s.account_key)
    assert await reopened.async_get(running.task_id, c) == running
    assert ticket_view(running)["state"] == "running"
    cancelled = await reopened.async_cancel(running.task_id, c)
    with pytest.raises(AssistantError, match="^retry_later$"):
        await s.async_checkpoint(running, TaskProgress("queued", 0, 0, dt_util.utcnow()), c)
    assert not cancelled.execution_valid(dt_util.utcnow())
    assert await s.async_get(running.task_id, c) == cancelled


async def test_checkpoint_uses_durable_attempts_and_cancel_keeps_actual_consumption(
    hass, entry, task_store
):
    from custom_components.ninebot.assistant_approval import TaskProgress
    from custom_components.ninebot.preservation import DetailBudget

    result = await seeded_request(hass, entry, task_store)
    s, c = entry.runtime_data.assistant_tasks, task_store.context
    ticket = await s.async_get(result["task"]["task_id"], c)
    approved = await approve_task(s, ticket, c)
    with pytest.raises(ValueError, match="durable attempt"):
        await s.async_checkpoint(approved, TaskProgress("running", 0, 1, dt_util.utcnow()), c)
    archive = task_store.co.statistics.archive
    claimed = await archive.async_preservation_claim(
        "SyntheticSN",
        dt_util.utcnow(),
        0,
        scope=ticket.plan.scope,
        task_budget=DetailBudget(ticket.task_id, ticket.plan.attempts),
        guard=lambda: True,
    )
    assert claimed is not None
    assert (
        await archive.async_detail_budget_used(
            "SyntheticSN",
            ticket.plan.scope,
            DetailBudget(ticket.task_id, ticket.plan.attempts),
            guard=lambda: True,
        )
        == 1
    )
    cancelled = await s.async_cancel(ticket.task_id, c)
    assert cancelled.progress.attempts == 1


async def test_completed_checkpoint_requires_actual_saved_payload(hass, entry, task_store):
    from custom_components.ninebot.assistant_approval import TaskProgress
    from custom_components.ninebot.raw import Endpoint, build_record

    result = await seeded_request(hass, entry, task_store)
    s, c = entry.runtime_data.assistant_tasks, task_store.context
    ticket = await s.async_get(result["task"]["task_id"], c)
    approved = await approve_task(s, ticket, c)
    completed = TaskProgress("completed", 1, 0, dt_util.utcnow())
    with pytest.raises(AssistantError, match="^local_data_missing$"):
        await s.async_checkpoint(approved, completed, c)
    assert (await s.async_get(ticket.task_id, c)).progress is None
    archive = task_store.co.statistics.archive
    month = await archive.async_month("SyntheticSN", "202611")
    ride = next(r for r in month.travel.rides if r.ride_id == ticket.plan.scope.rides[0][0])
    receipt = dt_util.utcnow() + timedelta(seconds=1)
    await archive.async_record_detail(
        "SyntheticSN", ride, receipt, raw=build_record(Endpoint.TRIP_DETAIL, {}, receipt, "202611")
    )
    saved = await s.async_checkpoint(approved, completed, c)
    assert saved.terminal and not saved.execution_valid(dt_util.utcnow())
    assert (await s.async_request(ticket.plan, c)).task_id != saved.task_id


async def test_cancel_can_revoke_when_budget_evidence_is_unreadable(task_store):
    from custom_components.ninebot.preservation import DetailBudgetUnavailable

    s, p, c = task_store.store, task_store.plan, task_store.context
    ticket = await approve_task(s, await s.async_request(p, c), c)
    with patch.object(
        task_store.co.statistics.archive,
        "async_detail_budget_used",
        new=AsyncMock(side_effect=DetailBudgetUnavailable()),
    ):
        cancelled = await s.async_cancel(ticket.task_id, c)
    assert cancelled.progress.attempts is None and not cancelled.execution_valid(dt_util.utcnow())


async def test_backup_retains_progress_without_restoring_execution_authority(hass, task_store):
    from custom_components.ninebot.assistant_approval import TaskProgress
    from custom_components.ninebot.assistant_task_views import ticket_view
    from custom_components.ninebot.backup import async_post_backup, async_pre_backup

    s, p, c = task_store.store, task_store.plan, task_store.context
    ticket = await approve_task(s, await s.async_request(p, c), c)
    waiting = await s.async_checkpoint(
        ticket, TaskProgress("waiting_budget", 0, 0, dt_util.utcnow()), c
    )
    await async_pre_backup(hass)
    raw = await hass.async_add_executor_job(Path(s._store.path).read_text)
    copy = restore_ticket(json.loads(raw)["data"]["tickets"][ticket.task_id])
    assert copy.progress == waiting.progress and copy.approved_at is None
    assert not copy.execution_valid(dt_util.utcnow())
    assert ticket_view(copy)["state"] == "awaiting_approval"
    await async_post_backup(hass)
    assert await s.async_get(ticket.task_id, c) == waiting


async def test_legacy_minor_one_record_reads_without_granting_new_authority(task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    ticket = await s.async_request(p, c)
    old = ticket_data(ticket)
    old.pop("progress")
    old.pop("context_id")
    from dataclasses import replace

    assert restore_ticket(old) == replace(ticket, context_id=None)
    envelope = {
        "version": 1,
        "minor_version": 1,
        "key": s.key,
        "data": {"tickets": {ticket.task_id: old}},
    }
    await s.hass.async_add_executor_job(Path(s._store.path).write_text, json.dumps(envelope))
    reopened = ApprovalStore(s.hass, s.entry_id, s.account_key)
    assert await reopened.async_get(ticket.task_id, c) == replace(ticket, context_id=None)
    assert ticket.progress is None and not ticket.execution_valid(dt_util.utcnow())


async def test_terminal_retention_does_not_block_new_requests_and_lists_are_bounded(
    hass, entry, task_store
):
    from custom_components.ninebot.assistant_review import async_register
    from custom_components.ninebot.assistant_task_queries import async_task_query

    async_register(hass)
    s, p, c = task_store.store, task_store.plan, task_store.context
    entry.runtime_data.assistant_tasks = s
    oldest = None
    for _ in range(25):
        ticket = await s.async_request(p, c)
        oldest = oldest or ticket.task_id
        await s.async_cancel(ticket.task_id, c)
    with pytest.raises(AssistantError, match="^task_not_found$"):
        await s.async_get(oldest, c)
    ids = set()
    for page, count in ((1, 10), (2, 10), (3, 4)):
        response, error = await async_task_query(
            hass, "get_tasks", {"vehicle_ref": p.vehicle_ref, "page": page}, c
        )
        assert not error
        data = response["data"]
        assert data["total"] == 24 and len(data["tasks"]) == count
        assert data["has_more"] is (page < 3)
        ids.update(row["task_id"] for row in data["tasks"])
    assert len(ids) == 24


async def test_policy_revoke_survives_delete_failure_and_reopened_owner(hass, entry, task_store):
    from custom_components.ninebot.assistant import TASK_POLICY_EPOCH
    from custom_components.ninebot.assistant_tasks import async_reset_tasks

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    entry.runtime_data.assistant_tasks = s
    old_epoch = entry.data[TASK_POLICY_EPOCH]
    original_file = await hass.async_add_executor_job(Path(s._store.path).read_bytes)
    with patch.object(s._store, "async_remove", new=AsyncMock()):
        with pytest.raises(TaskStoreUnavailable):
            await async_reset_tasks(hass, entry, remove=True)
    assert entry.data[TASK_POLICY_EPOCH] != old_epoch
    assert await hass.async_add_executor_job(Path(s._store.path).read_bytes) == original_file
    # Reopening from the now-current config cannot give the surviving old file authority.
    reopened = ApprovalStore(hass, s.entry_id, s.account_key)
    with pytest.raises(AssistantError, match="^task_not_found$"):
        await reopened.async_get(approved.task_id, c)
    fresh = await reopened.async_request(p, c)
    assert fresh.task_id != approved.task_id and fresh.approved_at is None


async def test_rapid_consent_off_on_is_observed_before_async_listener_lock(hass, entry, task_store):
    from custom_components.ninebot.assistant import TASK_POLICY_EPOCH

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    runtime = entry.runtime_data
    runtime.assistant_tasks = s
    options = dict(entry.options)
    first = entry.data[TASK_POLICY_EPOCH]
    async with runtime.options_update_lock:
        hass.config_entries.async_update_entry(
            entry, options={**options, "enable_assistant_tasks": False}
        )
        disabled = entry.data[TASK_POLICY_EPOCH]
        hass.config_entries.async_update_entry(entry, options=options)
        enabled = entry.data[TASK_POLICY_EPOCH]
        assert len({first, disabled, enabled}) == 3
        with pytest.raises(AssistantError, match="^not_authorized$"):
            await s.async_get(approved.task_id, c)
    await hass.async_block_till_done()
    assert runtime is entry.runtime_data and runtime.assistant_tasks is None
    assert runtime.applied_data[TASK_POLICY_EPOCH] == enabled
    assert runtime.applied_options == options
    assert runtime.assistant_task_blocks == 0


async def test_policy_epoch_stays_stable_for_noop_and_unrelated_hot_update(hass, entry, task_store):
    from custom_components.ninebot import async_update_options
    from custom_components.ninebot.assistant import TASK_POLICY_EPOCH

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    entry.runtime_data.assistant_tasks = s
    epoch = entry.data[TASK_POLICY_EPOCH]
    runtime = entry.runtime_data
    await async_update_options(hass, entry)
    assert epoch == entry.data[TASK_POLICY_EPOCH]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "polling_profile": "balanced"}
    )
    await hass.async_block_till_done()
    assert runtime is entry.runtime_data and epoch == entry.data[TASK_POLICY_EPOCH]
    assert await s.async_get(approved.task_id, c) == approved


async def test_legacy_approval_is_not_grandfathered_into_current_policy(hass, task_store):
    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    old = ticket_data(approved)
    envelope = {
        "version": 1,
        "minor_version": 2,
        "key": s.key,
        "data": {"tickets": {approved.task_id: old}},
    }
    await hass.async_add_executor_job(Path(s._store.path).write_text, json.dumps(envelope))
    reopened = ApprovalStore(hass, s.entry_id, s.account_key)
    recovered = await reopened.async_get(approved.task_id, c)
    assert recovered.plan == approved.plan and recovered.approved_at is None
    assert recovered.nonce_hash is None and not recovered.execution_valid(dt_util.utcnow())


async def test_epoch_revoke_and_surviving_old_file_are_durable_native_storage(
    hass, entry, task_store
):
    from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE

    from custom_components.ninebot.assistant import TASK_POLICY_EPOCH
    from custom_components.ninebot.assistant_tasks import async_reset_tasks

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    entry.runtime_data.assistant_tasks = s
    old_epoch = entry.data[TASK_POLICY_EPOCH]
    with patch.object(s._store, "async_remove", new=AsyncMock()):
        with pytest.raises(TaskStoreUnavailable):
            await async_reset_tasks(hass, entry, remove=True)
        await hass.async_block_till_done()
    # Flush HA's real pending writer without advancing unrelated vehicle schedules.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    config_path = Path(hass.config.path(".storage", "core.config_entries"))
    saved = json.loads(await hass.async_add_executor_job(config_path.read_text))
    row = next(r for r in saved["data"]["entries"] if r["entry_id"] == entry.entry_id)
    assert row["data"][TASK_POLICY_EPOCH] == entry.data[TASK_POLICY_EPOCH] != old_epoch
    assert row["options"]["enable_assistant_tasks"] is True
    surviving = json.loads(await hass.async_add_executor_job(Path(s._store.path).read_text))
    assert surviving["data"]["policy_epoch"] == old_epoch
    assert surviving["data"]["tickets"][approved.task_id]["approved_at"] is not None
    reopened = ApprovalStore(hass, s.entry_id, s.account_key)
    assert await reopened.async_list(c, p.vehicle_ref) == ()


async def test_ordinary_reload_keeps_epoch_and_approved_ticket(hass, entry, task_store):
    from custom_components.ninebot.assistant import TASK_POLICY_EPOCH

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    entry.runtime_data.assistant_tasks = s
    epoch = entry.data[TASK_POLICY_EPOCH]
    with patch(
        "custom_components.ninebot.coordinator.NinebotCoordinator.async_config_entry_first_refresh",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.coordinator.realtime.stop()
    assert entry.runtime_data.coordinator is not task_store.co
    assert entry.data[TASK_POLICY_EPOCH] == epoch
    reopened = ApprovalStore(hass, s.entry_id, s.account_key)
    assert await reopened.async_get(approved.task_id, c) == approved


async def execution_tick(hass, co):
    await hass.async_block_till_done()
    with patch.object(AssistantExecution, "async_step", new=REAL_EXECUTION_STEP):
        await co.preservation.async_tick(dt_util.utcnow(), assistant_only=True)
    await hass.async_block_till_done()


async def queued_execution(hass, entry, case):
    from custom_components.ninebot.assistant_execution import driver_for

    result = await seeded_request(hass, entry, case)
    store = entry.runtime_data.assistant_tasks
    pending = await store.async_get(result["task"]["task_id"], case.context)
    approved = await approve_task(store, pending, case.context)
    driver = driver_for(hass, entry)
    ticket = await driver.async_accept(approved, case.context)
    await hass.async_block_till_done()
    return driver, ticket


def execution_payload():
    return {
        "travel_id": "private-task-ride",
        "duration": 600,
        "mileages": 1.2,
        "ec": 15,
        "trail": "10,20,12,0;10.001,20.001,15,1",
    }


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_approved_scope_executes_persists_and_stops_without_auto_history(
    hass, entry, task_store
):
    driver, ticket = await queued_execution(hass, entry, task_store)
    task_store.client.async_get_trip_detail.return_value = execution_payload()
    co = task_store.co
    with (
        patch.object(co.preservation.bootstrap, "async_seed", new=AsyncMock()) as bootstrap,
        patch.object(co.preservation.incremental, "async_seed", new=AsyncMock()) as incremental,
        patch.object(co.preservation.months, "async_seed_recent", new=AsyncMock()) as months,
        patch.object(co.preservation.audit, "async_step", new=AsyncMock()) as audit,
        patch.object(co.preservation.reconstruction, "async_step", new=AsyncMock()) as reconstruct,
    ):
        await execution_tick(hass, co)
        for unrelated in (bootstrap, incremental, months, audit, reconstruct):
            unrelated.assert_not_awaited()
    finished = await driver.store.async_get(ticket.task_id, task_store.context)
    assert finished.progress.state == "completed" and finished.progress.saved == 1
    assert finished.progress.attempts == 1 and ticket.plan.attempts == 2
    assert task_store.client.async_get_trip_detail.await_count == 1
    assert not driver.has_work and co.preservation._remove is None
    detail = await co.statistics.archive.async_private_detail("SyntheticSN", "private-task-ride")
    assert detail.latest is not None and detail.ride.total_track_points == 2
    assert entry.options.get("preserve_history", False) is False


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_approved_job_reuses_a_detail_saved_by_another_consumer(hass, entry, task_store):
    from custom_components.ninebot.raw import Endpoint, build_record

    driver, ticket = await queued_execution(hass, entry, task_store)
    archive = task_store.co.statistics.archive
    month = await archive.async_month("SyntheticSN", "202611")
    ride = next(r for r in month.travel.rides if r.ride_id == "private-task-ride")
    receipt = dt_util.utcnow()
    await archive.async_record_detail(
        "SyntheticSN",
        ride,
        receipt,
        raw=build_record(Endpoint.TRIP_DETAIL, execution_payload(), receipt, "202611"),
    )
    await execution_tick(hass, task_store.co)
    actual = await driver.store.async_get(ticket.task_id, task_store.context)
    assert actual.progress.state == "completed" and actual.progress.attempts == 0
    task_store.client.async_get_trip_detail.assert_not_awaited()


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_task_waits_for_account_budget_then_finishes(hass, entry, task_store):
    driver, ticket = await queued_execution(hass, entry, task_store)
    archive = task_store.co.statistics.archive
    assert await archive.async_background_budget(
        "SyntheticSN", dt_util.utcnow(), 60, guard=lambda: True
    )
    await execution_tick(hass, task_store.co)
    waiting = await driver.store.async_get(ticket.task_id, task_store.context)
    assert waiting.progress.state == "waiting_budget" and waiting.progress.retry_at is not None
    assert waiting.progress.attempts == 0
    task_store.client.async_get_trip_detail.assert_not_awaited()

    # Elapse just the stored account deadline, not unrelated HA entity timers.
    def elapse():
        db = archive._db(write=True)
        with db:
            db.execute(
                "UPDATE meta SET value=? WHERE key='preservation_budget'",
                (str(dt_util.utcnow().timestamp() - 1),),
            )

    await archive._run(elapse)
    task_store.client.async_get_trip_detail.return_value = execution_payload()
    await execution_tick(hass, task_store.co)
    assert (
        await driver.store.async_get(ticket.task_id, task_store.context)
    ).progress.state == "completed"
    assert task_store.client.async_get_trip_detail.await_count == 1


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_task_failure_retries_within_approved_cap(hass, entry, task_store):
    from custom_components.ninebot.exceptions import ErrorKind, NinebotError
    from custom_components.ninebot.ride_archive import vehicle_key

    driver, ticket = await queued_execution(hass, entry, task_store)
    task_store.client.async_get_trip_detail.side_effect = [
        NinebotError(ErrorKind.CONNECTION),
        execution_payload(),
    ]
    await execution_tick(hass, task_store.co)
    failed = await driver.store.async_get(ticket.task_id, task_store.context)
    assert failed.progress.state == "retry_wait" and failed.progress.attempts == 1
    assert failed.progress.retry_at is not None
    archive = task_store.co.statistics.archive

    def elapse():
        db = archive._db(write=True)
        with db:
            db.execute("UPDATE meta SET value=? WHERE key='preservation_budget'", ("0",))
            key = f"preservation:{vehicle_key('SyntheticSN')}:private-task-ride"
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            value = json.loads(row[0])
            value["next_attempt"] = 0
            db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(value), key))

    await archive._run(elapse)
    await execution_tick(hass, task_store.co)
    done = await driver.store.async_get(ticket.task_id, task_store.context)
    assert done.progress.state == "completed" and done.progress.attempts == 2
    assert task_store.client.async_get_trip_detail.await_count == 2


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_cancel_inflight_task_does_not_commit_late_detail(hass, entry, task_store):
    driver, ticket = await queued_execution(hass, entry, task_store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def query(*args, **kwargs):
        entered.set()
        await release.wait()
        return execution_payload()

    task_store.client.async_get_trip_detail.side_effect = query
    with patch.object(AssistantExecution, "async_step", new=REAL_EXECUTION_STEP):
        tick = asyncio.create_task(
            task_store.co.preservation.async_tick(dt_util.utcnow(), assistant_only=True)
        )
        await asyncio.wait_for(entered.wait(), 10)
        cancelled = await driver.store.async_cancel(ticket.task_id, task_store.context)
        release.set()
        await tick
    assert cancelled.progress.state == "cancelled"
    assert (
        await driver.store.async_get(ticket.task_id, task_store.context)
    ).progress.state == "cancelled"
    assert (
        await task_store.co.statistics.archive.async_private_detail(
            "SyntheticSN", "private-task-ride"
        )
        is None
    )
    assert task_store.client.async_get_trip_detail.await_count == 1


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_requester_access_revoked_before_tick_prevents_cloud(hass, entry, task_store):
    driver, ticket = await queued_execution(hass, entry, task_store)
    with patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=None)):
        await execution_tick(hass, task_store.co)
    failed = await driver.store.async_get(ticket.task_id, task_store.context)
    assert failed.progress.state == "failed" and failed.progress.reason == "not_authorized"
    task_store.client.async_get_trip_detail.assert_not_awaited()


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_execution_resumes_persisted_principal_and_cap_after_owner_reopen(
    hass, entry, task_store
):
    from custom_components.ninebot.assistant_execution import driver_for
    from custom_components.ninebot.assistant_tasks import async_reset_tasks

    driver, ticket = await queued_execution(hass, entry, task_store)
    context_id = ticket.context_id
    await async_reset_tasks(hass, entry, remove=False)
    resumed = driver_for(hass, entry)
    await resumed.async_restore()
    await hass.async_block_till_done()
    assert resumed.contexts[ticket.task_id].user_id == task_store.plan.user_id
    assert resumed.contexts[ticket.task_id].parent_id == context_id
    task_store.client.async_get_trip_detail.return_value = execution_payload()
    await execution_tick(hass, task_store.co)
    actual = await resumed.store.async_get(ticket.task_id, task_store.context)
    assert actual.progress.state == "completed" and actual.progress.attempts == 1
    assert task_store.client.async_get_trip_detail.await_count == 1


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_execution_never_reinitializes_missing_approved_counter(hass, entry, task_store):
    from custom_components.ninebot.preservation import DetailBudget

    driver, ticket = await queued_execution(hass, entry, task_store)
    archive = task_store.co.statistics.archive

    def remove():
        db = archive._db(write=True)
        with db:
            db.execute("DELETE FROM meta WHERE key=?", (DetailBudget(ticket.task_id, 2).key,))

    await archive._run(remove)
    await execution_tick(hass, task_store.co)
    actual = await driver.store.async_get(ticket.task_id, task_store.context)
    assert actual.progress.state == "failed" and actual.progress.reason == "budget_unavailable"
    assert actual.progress.attempts is None
    task_store.client.async_get_trip_detail.assert_not_awaited()


@pytest.mark.parametrize("invalid", [False, 0, "", [], {}])
async def test_retry_deadline_codec_rejects_false_empty_and_non_timestamps(task_store, invalid):
    from custom_components.ninebot.assistant_approval import TaskProgress

    s, p, c = task_store.store, task_store.plan, task_store.context
    ticket = await approve_task(s, await s.async_request(p, c), c)
    raw = ticket_data(ticket)
    raw["progress"] = {
        "state": "waiting_budget",
        "saved": 0,
        "attempts": 0,
        "updated_at": dt_util.utcnow().isoformat(),
        "reason": None,
        "retry_at": invalid,
    }
    with pytest.raises(TaskStoreUnavailable):
        restore_ticket(raw)
    # A true omitted deadline is still a valid old progress record.
    raw["progress"].pop("retry_at")
    assert restore_ticket(raw).progress == TaskProgress(
        "waiting_budget", 0, 0, dt_util.parse_datetime(raw["progress"]["updated_at"])
    )


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_task_exhaustion_stops_requests_and_terminal_state_survives_reload(
    hass, entry, task_store
):
    from custom_components.ninebot.exceptions import ErrorKind, NinebotError
    from custom_components.ninebot.ride_archive import vehicle_key

    driver, ticket = await queued_execution(hass, entry, task_store)
    task_store.client.async_get_trip_detail.side_effect = NinebotError(ErrorKind.CONNECTION)
    await execution_tick(hass, task_store.co)
    archive = task_store.co.statistics.archive

    def elapse():
        db = archive._db(write=True)
        with db:
            db.execute("UPDATE meta SET value='0' WHERE key='preservation_budget'")
            key = f"preservation:{vehicle_key('SyntheticSN')}:private-task-ride"
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            raw = json.loads(row[0])
            raw["next_attempt"] = 0
            db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(raw), key))

    await archive._run(elapse)
    await execution_tick(hass, task_store.co)
    finished = await driver.store.async_get(ticket.task_id, task_store.context)
    assert finished.progress.state == "failed" and finished.progress.reason == "budget_exhausted"
    assert finished.progress.attempts == 2 and not driver.has_work
    await driver.async_restore()
    await execution_tick(hass, task_store.co)
    assert not driver.has_work and task_store.co.preservation._remove is None
    assert task_store.client.async_get_trip_detail.await_count == 2


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_durable_approval_survives_temporary_queue_checkpoint_pressure(
    hass, entry, task_store
):
    from custom_components.ninebot.assistant_execution import driver_for
    from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure

    result = await seeded_request(hass, entry, task_store)
    store = entry.runtime_data.assistant_tasks
    pending = await store.async_get(result["task"]["task_id"], task_store.context)
    approved = await approve_task(store, pending, task_store.context)
    driver = driver_for(hass, entry)
    with patch.object(
        task_store.co.statistics.archive,
        "async_detail_budget_used",
        new=AsyncMock(side_effect=ArchiveError(ArchiveFailure.BUSY)),
    ):
        with pytest.raises(ArchiveError):
            await driver.async_accept(approved, task_store.context)
        await hass.async_block_till_done()
    assert driver.has_work
    task_store.client.async_get_trip_detail.return_value = execution_payload()
    await execution_tick(hass, task_store.co)
    assert (
        await store.async_get(approved.task_id, task_store.context)
    ).progress.state == "completed"
    assert task_store.client.async_get_trip_detail.await_count == 1


@pytest.mark.parametrize("state", ["paused", "waiting_budget", "retry_wait"])
async def test_unchanged_checkpoint_revalidates_but_does_not_rewrite_store(
    hass, task_store, freezer, state
):
    from dataclasses import replace

    from custom_components.ninebot.assistant_approval import TaskProgress

    s, p, c = task_store.store, task_store.plan, task_store.context
    approved = await approve_task(s, await s.async_request(p, c), c)
    checkpoint = await s.async_checkpoint(approved, TaskProgress(state, 0, 0, dt_util.utcnow()), c)
    before = await hass.async_add_executor_job(Path(s._store.path).read_bytes)
    freezer.move_to(dt_util.utcnow() + timedelta(seconds=2))
    progress = replace(checkpoint.progress, updated_at=dt_util.utcnow())
    with patch.object(s._store, "async_save", new=AsyncMock()) as write:
        actual = await s.async_checkpoint(checkpoint, progress, c)
        write.assert_not_awaited()
    assert actual == checkpoint and actual.progress.updated_at < dt_util.utcnow()
    assert await hass.async_add_executor_job(Path(s._store.path).read_bytes) == before
    # Unchanged values must not turn into an authorization shortcut.
    with patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=None)):
        with pytest.raises((AssistantError, Unauthorized)):
            await s.async_checkpoint(checkpoint, progress, c)


@pytest.mark.parametrize("approved", [False, True])
async def test_human_review_cancel_is_idempotent_and_retires_only_own_work(
    hass, entry, task_store, review_client, hass_admin_user, approved
):
    from custom_components.ninebot.assistant_execution import driver_for

    context = Context(user_id=hass_admin_user.id)
    application = await seeded_request(hass, entry, task_store, context=context)
    device, task_id = task_store.plan.vehicle_ref, application["task"]["task_id"]
    store = entry.runtime_data.assistant_tasks
    pending = await store.async_get(task_id, context)
    driver = driver_for(hass, entry)
    if approved:
        ticket = await approve_task(store, pending, context)
        await driver.async_accept(ticket, context)
        await hass.async_block_till_done()
        assert driver.has_work and task_store.co.preservation._remove is not None
    first = await review(review_client, device, 101, "cancel", task_id=task_id)
    assert first["success"] and first["result"]["task"]["state"] == "cancelled"
    assert "token" not in str(first) and not driver.has_work
    assert task_store.co.preservation._remove is None
    second = await review(review_client, device, 102, "cancel", task_id=task_id)
    assert second["success"] and second["result"] == first["result"]
    malformed = await review(review_client, device, 103, "cancel", task_id=task_id, token="x" * 32)
    assert malformed["error"]["code"] == "invalid_arguments"
    assert entry.options.get("preserve_history", False) is False


async def test_human_review_cancel_rejects_another_requester(
    hass, entry, task_store, review_client
):
    result = await seeded_request(hass, entry, task_store)
    denied = await review(
        review_client, task_store.plan.vehicle_ref, 110, "cancel", task_id=result["task"]["task_id"]
    )
    assert denied["error"]["code"] == "not_authorized"
    pending = await entry.runtime_data.assistant_tasks.async_get(
        result["task"]["task_id"], task_store.context
    )
    assert pending.progress is None


async def test_human_cancel_keeps_another_approved_task_scheduled(
    hass, entry, task_store, review_client, hass_admin_user
):
    from custom_components.ninebot.assistant_execution import driver_for
    from custom_components.ninebot.assistant_tasks import async_request_details

    context = Context(user_id=hass_admin_user.id)
    first = await seeded_request(hass, entry, task_store, context=context)
    device = task_store.plan.vehicle_ref
    second = await async_request_details(hass, context, device, "2026-11-05", "2026-11-06")
    first_id, second_id = first["task"]["task_id"], second["task"]["task_id"]
    assert first_id != second_id
    driver = driver_for(hass, entry)
    for task_id in (first_id, second_id):
        pending = await driver.store.async_get(task_id, context)
        approved = await approve_task(driver.store, pending, context)
        await driver.async_accept(approved, context)
    await hass.async_block_till_done()
    cancelled = await review(review_client, device, 120, "cancel", task_id=first_id)
    assert cancelled["success"] and cancelled["result"]["task"]["state"] == "cancelled"
    assert set(driver.tickets) == {second_id}
    assert driver.has_work and task_store.co.preservation._remove is not None
    remaining = await driver.store.async_get(second_id, context)
    assert remaining.progress.state == "queued"
    assert not (
        await task_store.co.statistics.archive.async_detail_scope_state(
            "SyntheticSN", remaining.plan.scope, guard=lambda: True
        )
    ).paused
    stopped = await review(review_client, device, 121, "cancel", task_id=second_id)
    assert stopped["success"] and not driver.has_work
    assert task_store.co.preservation._remove is None


async def test_human_review_vehicle_discovery_is_local_scoped_and_revocable(
    hass, entry, task_store, review_client
):
    await review_client.send_json({"id": 150, "type": "ninebot/assistant/vehicles"})
    result = await asyncio.wait_for(review_client.receive_json(), 10)
    assert result["success"]
    data = result["result"]
    assert data["data"]["vehicles"][0]["vehicle_ref"] == task_store.plan.vehicle_ref
    assert data["evidence"]["cloud_calls"] == 0
    assert "SyntheticSN" not in json.dumps(data)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "enable_assistant_tasks": False}
    )
    await hass.async_block_till_done()
    await review_client.send_json({"id": 151, "type": "ninebot/assistant/vehicles"})
    denied = await asyncio.wait_for(review_client.receive_json(), 10)
    assert denied["error"]["code"] == "not_authorized"


async def native_notice(client, entry_id, identity):
    from custom_components.ninebot.assistant_notifications import notification_id

    await client.send_json({"id": identity, "type": "persistent_notification/get"})
    response = await asyncio.wait_for(client.receive_json(), 10)
    assert response["success"]
    return next(
        (row for row in response["result"] if row["notification_id"] == notification_id(entry_id)),
        None,
    )


@pytest.mark.parametrize(
    "language,ready,identity,attention",
    [
        ("en", "ready for review", "same Home Assistant user", "needs attention"),
        ("zh-Hans", "等待审核", "同一个 Home Assistant 用户", "需要处理"),
    ],
)
async def test_native_notices_follow_saved_request_approval_and_failure_without_private_fields(
    hass, entry, task_store, review_client, language, ready, identity, attention
):
    from homeassistant.components import persistent_notification
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "persistent_notification", {})
    s, p, c = task_store.store, task_store.plan, task_store.context
    entry.runtime_data.assistant_tasks = s
    hass.config.language = language
    with patch.object(
        persistent_notification, "async_create", wraps=persistent_notification.async_create
    ) as create:
        pending = await s.async_request(p, c)
        notice = await native_notice(review_client, entry.entry_id, 201)
        assert notice is not None and notice["title"] == "Ninebot Assistant"
        assert ready in notice["message"] and identity in notice["message"]
        for private in (
            entry.entry_id,
            p.sn,
            p.account_key,
            p.user_id,
            p.vehicle_ref,
            pending.task_id,
            p.start_date.isoformat(),
        ):
            assert private not in json.dumps(notice)
        _, nonce = await s.async_prepare(pending.task_id, c)
        assert nonce not in json.dumps(notice)
        await s.async_request(p, c)
        assert create.call_count == 1  # Neither nonce rotation nor application dedup notifies.
        approved = await s.async_approve(pending.task_id, c, nonce, p.digest)
        assert await native_notice(review_client, entry.entry_id, 202) is None
        from custom_components.ninebot.assistant_approval import TaskProgress

        waiting = await s.async_checkpoint(
            approved, TaskProgress("waiting_budget", 0, 0, dt_util.utcnow()), c
        )
        assert create.call_count == 1
        await s.async_fail_execution(waiting, "budget_unavailable")
        failed = await native_notice(review_client, entry.entry_id, 203)
        assert attention in failed["message"] and create.call_count == 2


async def test_other_pending_request_keeps_one_generic_notice_without_progress_spam(
    hass, entry, task_store, review_client
):
    from dataclasses import replace

    from homeassistant.components import persistent_notification
    from homeassistant.setup import async_setup_component

    from custom_components.ninebot.assistant_approval import TaskProgress

    assert await async_setup_component(hass, "persistent_notification", {})
    s, p, c = task_store.store, task_store.plan, task_store.context
    entry.runtime_data.assistant_tasks = s
    first = await s.async_request(p, c)
    second = await s.async_request(replace(p, start_date=p.start_date - timedelta(days=1)), c)
    approved = await approve_task(s, first, c)
    with patch.object(
        persistent_notification, "async_create", wraps=persistent_notification.async_create
    ) as create:
        running = await s.async_checkpoint(
            approved, TaskProgress("running", 0, 0, dt_util.utcnow()), c
        )
        await s.async_checkpoint(running, TaskProgress("retry_wait", 0, 0, dt_util.utcnow()), c)
        create.assert_not_called()
    assert (
        "ready for review" in (await native_notice(review_client, entry.entry_id, 211))["message"]
    )
    await s.async_cancel(first.task_id, c)
    assert (
        "ready for review" in (await native_notice(review_client, entry.entry_id, 212))["message"]
    )
    await s.async_cancel(second.task_id, c)
    assert await native_notice(review_client, entry.entry_id, 213) is None


@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_completion_notice_requires_actual_saved_detail(
    hass, entry, task_store, review_client
):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "persistent_notification", {})
    driver, ticket = await queued_execution(hass, entry, task_store)
    assert await native_notice(review_client, entry.entry_id, 221) is None
    task_store.client.async_get_trip_detail.return_value = execution_payload()
    await execution_tick(hass, task_store.co)
    actual = await driver.store.async_get(ticket.task_id, task_store.context)
    assert actual.progress.state == "completed" and actual.progress.saved == 1
    assert "has finished" in (await native_notice(review_client, entry.entry_id, 222))["message"]
    assert task_store.client.async_get_trip_detail.await_count == 1


async def test_failed_task_write_does_not_publish_a_notice(hass, entry, task_store, review_client):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "persistent_notification", {})
    entry.runtime_data.assistant_tasks = task_store.store
    with patch.object(task_store.store._store, "async_save", new=AsyncMock()):
        with pytest.raises(TaskStoreUnavailable):
            await task_store.store.async_request(task_store.plan, task_store.context)
    assert await native_notice(review_client, entry.entry_id, 231) is None


async def test_revoke_dismisses_notice_even_if_task_file_removal_fails(
    hass, entry, task_store, review_client
):
    from homeassistant.setup import async_setup_component

    from custom_components.ninebot.assistant_tasks import async_reset_tasks

    assert await async_setup_component(hass, "persistent_notification", {})
    s = entry.runtime_data.assistant_tasks = task_store.store
    await s.async_request(task_store.plan, task_store.context)
    assert await native_notice(review_client, entry.entry_id, 241)
    with patch.object(s._store, "async_remove", new=AsyncMock()):
        with pytest.raises(TaskStoreUnavailable):
            await async_reset_tasks(hass, entry, remove=True)
    assert s.closed and await native_notice(review_client, entry.entry_id, 242) is None


async def test_notice_failure_does_not_turn_a_saved_application_into_an_error(
    hass, entry, task_store
):
    entry.runtime_data.assistant_tasks = task_store.store
    with patch(
        "custom_components.ninebot.assistant_task_store.async_import_module",
        new=AsyncMock(side_effect=ImportError("synthetic unavailable notification")),
    ):
        pending = await task_store.store.async_request(task_store.plan, task_store.context)
    assert await task_store.store.async_get(pending.task_id, task_store.context) == pending


async def test_policy_revocation_during_notice_import_cannot_resurrect_a_notice(
    hass, entry, task_store, review_client
):
    from homeassistant.helpers.importlib import async_import_module
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "persistent_notification", {})
    s = entry.runtime_data.assistant_tasks = task_store.store
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        entered.set()
        await release.wait()
        return await async_import_module(*args)

    with patch(
        "custom_components.ninebot.assistant_task_store.async_import_module", side_effect=delayed
    ):
        pending = asyncio.create_task(s.async_request(task_store.plan, task_store.context))
        await asyncio.wait_for(entered.wait(), 10)
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "enable_assistant_tasks": False}
        )
        release.set()
        with pytest.raises(AssistantError):
            await pending
        await hass.async_block_till_done()
    assert await native_notice(review_client, entry.entry_id, 251) is None


async def test_repeated_application_reports_existing_queued_state_not_new_approval(
    hass, entry, task_store
):
    from custom_components.ninebot.assistant_tasks import async_request_details

    driver, ticket = await queued_execution(hass, entry, task_store)
    response = await async_request_details(
        hass, task_store.context, ticket.plan.vehicle_ref, "2026-11-06", "2026-11-06"
    )
    assert response["approval_required"] is False
    assert response["task"] == {"task_id": ticket.task_id, "state": "queued"}
    assert set(driver.tickets) == {ticket.task_id}


@pytest.mark.skipif(
    os.environ.get("NINEBOT_NATIVE_BROWSER") != "1", reason="milestone native browser acceptance"
)
@pytest.mark.parametrize("task_store", [True], indirect=True)
async def test_native_browser_approves_executes_and_reads_saved_result(
    hass, entry, task_store, hass_client, hass_admin_user, hass_access_token, tmp_path
):
    """One real HA WS/browser/SQLite chain, with only the upstream client synthetic."""
    from aiohttp import web
    from homeassistant.setup import async_setup_component

    from custom_components.ninebot.assistant_review import async_register
    from custom_components.ninebot.frontend import async_register_frontend

    assert await async_setup_component(hass, "websocket_api", {})
    async_register(hass)
    await async_register_frontend(hass)

    async def page(_request):
        return web.Response(
            text='<!doctype html><html><meta charset="utf-8"><body>'
            '<script type="module" src="/ninebot-static/assistant-panel.js"></script>'
            "</body></html>",
            content_type="text/html",
        )

    hass.http.app.router.add_get("/assistant-browser-test", page)
    client = await hass_client()
    context = Context(user_id=hass_admin_user.id)
    application = await seeded_request(hass, entry, task_store, context=context)
    task_store.client.async_get_trip_detail.return_value = execution_payload()
    process = await asyncio.create_subprocess_exec(
        "node",
        "tests/browser/assistant-native.mjs",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    # Synthetic short-lived HA token travels only through the test child's stdin.
    process.stdin.write(
        json.dumps(
            {
                "url": str(client.make_url("/assistant-browser-test")),
                "token": hass_access_token,
                "user": hass_admin_user.id,
            }
        ).encode()
        + b"\n"
    )
    await process.stdin.drain()
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 40) == b"approved\n"
        await execution_tick(hass, task_store.co)
        ticket = await entry.runtime_data.assistant_tasks.async_get(
            application["task"]["task_id"], context
        )
        assert ticket.progress.state == "completed" and ticket.progress.saved == 1
        assert ticket.progress.attempts == 1
        detail = await task_store.co.statistics.archive.async_private_detail(
            "SyntheticSN", "private-task-ride"
        )
        assert detail.latest is not None and detail.ride.total_track_points == 2
        task_store.client.async_get_trip_detail.assert_awaited_once()
        process.stdin.write(b"continue\n")
        await process.stdin.drain()
        stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
        assert process.returncode == 0, stderr.decode()
        assert stdout == b"completed\n"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
