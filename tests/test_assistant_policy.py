"""Revocable AI consent does not inherit trusted automation exceptions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr

from custom_components.ninebot.assistant_policy import AssistantError, async_authorize

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def assistant_device(hass, entry, app_client):
    return await setup_assistant_device(hass, entry)


@pytest.fixture
async def proposal_device(hass, entry, app_client, freezer):
    # Establish the clock before setup; later receipts must supersede setup's month.
    freezer.move_to("2026-11-06T23:59:59+00:00")
    return await setup_assistant_device(hass, entry)


async def setup_assistant_device(hass, entry):
    # Registration is isolated from this authorization test on every Core.
    with patch("custom_components.ninebot.async_update_assistant_api", new=AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "enable_assistant": True,
            "assistant_vehicles": ["SyntheticSN"],
        },
    )
    await hass.async_block_till_done()
    return next(
        device.id
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in device.identifiers
    )


def user(*, active=True, admin=True):
    return SimpleNamespace(
        is_active=active,
        is_admin=admin,
        permissions=SimpleNamespace(access_all_entities=Mock(return_value=True)),
    )


@pytest.mark.parametrize("context", [None, Context()])
async def test_no_user_is_not_a_trusted_automation(hass, assistant_device, context):
    with pytest.raises(AssistantError, match="^not_authorized$"):
        await async_authorize(hass, context, assistant_device)


@pytest.mark.parametrize("principal", [None, user(active=False), user(admin=False)])
async def test_inactive_deleted_and_nonadmin_denied(hass, assistant_device, principal):
    with (
        patch.object(hass.auth, "async_get_user", return_value=principal),
        pytest.raises(AssistantError, match="^not_authorized$"),
    ):
        await async_authorize(hass, Context(user_id="synthetic"), assistant_device)


@pytest.mark.parametrize("change", ["option", "allowlist", "generation", "ownership", "device"])
async def test_inflight_result_rechecks_current_consent_and_ownership(
    hass, entry, assistant_device, change
):
    context = Context(user_id="synthetic")
    with patch.object(hass.auth, "async_get_user", return_value=user()):
        scope = await async_authorize(hass, context, assistant_device)
        assert scope.context is context
        if change == "option":
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, "enable_assistant": False}
            )
        elif change == "allowlist":
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, "assistant_vehicles": []}
            )
        elif change == "generation":
            scope.runtime.coordinator._generation += 1
        elif change == "ownership":
            scope.runtime.coordinator._ownership[scope.sn] = scope.ownership + 1
        else:
            dr.async_get(hass).async_update_device(
                assistant_device, disabled_by=dr.DeviceEntryDisabler.USER
            )
        with pytest.raises(AssistantError, match="^not_authorized$"):
            await scope.async_validate(hass)


async def test_return_guard_rechecks_user_and_retains_original_context(hass, assistant_device):
    context = Context(user_id="synthetic")
    with patch.object(hass.auth, "async_get_user", return_value=user()) as lookup:
        scope = await async_authorize(hass, context, assistant_device)
        lookup.return_value = user(active=False)
        with pytest.raises(AssistantError, match="^not_authorized$"):
            await scope.async_validate(hass)


async def test_consent_revoked_during_await_is_not_returned(hass, entry, assistant_device):
    context = Context(user_id="synthetic")
    with patch.object(hass.auth, "async_get_user", return_value=user()) as lookup:
        scope = await async_authorize(hass, context, assistant_device)

        async def revoke(user_id):
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, "enable_assistant": False}
            )
            return user()

        lookup.side_effect = revoke
        with pytest.raises(AssistantError, match="^not_authorized$"):
            await scope.async_validate(hass)


@pytest.mark.parametrize(
    "scope_option",
    [
        {},
        {"enable_assistant": True},
        {"enable_assistant": True, "assistant_vehicles": ["foreign-vehicle"]},
    ],
)
async def test_control_consent_does_not_grant_ai_access(
    hass, entry, assistant_device, scope_option
):
    hass.config_entries.async_update_entry(
        entry,
        options={
            "enable_controls": True,
            "control_vehicles": ["SyntheticSN"],
            **scope_option,
        },
    )
    with (
        patch.object(hass.auth, "async_get_user", return_value=user()),
        pytest.raises(AssistantError, match="^not_authorized$"),
    ):
        await async_authorize(hass, Context(user_id="synthetic"), assistant_device)


async def test_options_default_off_empty_scope_and_existing_features_unchanged(
    hass, entry, app_client
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with patch("custom_components.ninebot.config_flow.native_api_supported", return_value=True):
        form = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {key.schema: key for key in form["data_schema"].schema}
    assert fields["enable_assistant"].default() is False
    assert fields["assistant_vehicles"].default() == []
    assert list(fields)[-1] == "debug_mode"
    assert "enable_assistant" not in entry.options


async def test_legacy_options_do_not_offer_an_unavailable_assistant(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with patch("custom_components.ninebot.config_flow.native_api_supported", return_value=False):
        form = await hass.config_entries.options.async_init(entry.entry_id)
    assert "enable_assistant" not in {key.schema for key in form["data_schema"].schema}


async def test_options_require_scope_then_preserve_it_when_disabled(hass, entry, app_client):
    with patch("custom_components.ninebot.async_update_assistant_api", new=AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        with patch("custom_components.ninebot.config_flow.native_api_supported", return_value=True):
            form = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                form["flow_id"], {"enable_assistant": True}
            )
            assert result["errors"] == {"assistant_vehicles": "assistant_vehicles_required"}
            result = await hass.config_entries.options.async_configure(
                form["flow_id"],
                {"enable_assistant": True, "assistant_vehicles": ["SyntheticSN"]},
            )
            assert result["type"].value == "create_entry"
            await hass.async_block_till_done()
            form = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                form["flow_id"], {"enable_assistant": False}
            )
            assert result["type"].value == "create_entry"
            assert result["data"]["assistant_vehicles"] == ["SyntheticSN"]
            assert result["data"]["enable_assistant"] is False


@pytest.mark.parametrize("actor", ["synthetic", "another-admin", None])
async def test_approval_uses_original_authenticated_requester(
    hass, entry, assistant_device, actor, freezer
):
    from datetime import UTC, date, datetime

    from custom_components.ninebot.assistant_approval import (
        ApprovalTicket,
        DetailPlan,
        async_approve_ticket,
    )
    from custom_components.ninebot.preservation import DetailScope

    now = datetime(2026, 11, 7, tzinfo=UTC)
    freezer.move_to(now)
    plan = DetailPlan(
        "synthetic",
        entry.runtime_data.coordinator.statistics.archive.owner,
        assistant_device,
        "SyntheticSN",
        date(2026, 11, 5),
        date(2026, 11, 6),
        DetailScope((("fixture-ride", "b" * 64),)),
        2,
        now,
    )
    ticket, nonce = ApprovalTicket.issue(plan, now)
    context = Context(user_id=actor)
    with patch.object(hass.auth, "async_get_user", return_value=user()):
        if actor != plan.user_id:
            with pytest.raises(AssistantError, match="^not_authorized$"):
                await async_approve_ticket(hass, ticket, context, nonce, plan.digest)
        else:
            approved = await async_approve_ticket(hass, ticket, context, nonce, plan.digest)
            assert approved.approved_at == now and approved.nonce_hash == ""


@pytest.mark.parametrize(
    "change,code", [("consent", "not_authorized"), ("expiry", "approval_expired")]
)
async def test_approval_rechecks_consent_after_awaited_permissions(
    hass, entry, assistant_device, freezer, change, code
):
    from datetime import UTC, date, datetime

    from custom_components.ninebot.assistant_approval import (
        ApprovalTicket,
        DetailPlan,
        async_approve_ticket,
    )
    from custom_components.ninebot.assistant_policy import AssistantScope
    from custom_components.ninebot.preservation import DetailScope

    now = datetime(2026, 11, 7, tzinfo=UTC)
    freezer.move_to(now)
    plan = DetailPlan(
        "synthetic",
        entry.runtime_data.coordinator.statistics.archive.owner,
        assistant_device,
        "SyntheticSN",
        date(2026, 11, 5),
        date(2026, 11, 6),
        DetailScope((("fixture-ride", "b" * 64),)),
        2,
        now,
    )
    ticket, nonce = ApprovalTicket.issue(plan, now)
    original = AssistantScope.async_validate

    async def revoke(scope, actual_hass):
        await original(scope, actual_hass)
        if change == "consent":
            actual_hass.config_entries.async_update_entry(
                entry, options={**entry.options, "enable_assistant": False}
            )
        else:
            freezer.tick(600)

    with (
        patch.object(hass.auth, "async_get_user", return_value=user()),
        patch.object(AssistantScope, "async_validate", new=revoke),
        pytest.raises(AssistantError, match=f"^{code}$"),
    ):
        await async_approve_ticket(hass, ticket, Context(user_id="synthetic"), nonce, plan.digest)


def proposal_month(count=2):
    from datetime import UTC, datetime

    from custom_components.ninebot.adapters import travel

    start = int(datetime(2026, 11, 6, 2, tzinfo=UTC).timestamp())
    return travel(
        {
            "times": count,
            "list": [
                {
                    "travel_id": f"private-ride-{i}",
                    "start_time": start + i * 1000,
                    "end_time": start + i * 1000 + 600,
                    "duration": 600,
                    "mileages": 1.2,
                }
                for i in range(count)
            ],
        },
        "202611",
    )


async def test_local_proposal_binds_original_caller_and_projects_no_private_plan(
    hass, entry, proposal_device, app_client, freezer
):
    import hashlib
    import json
    from datetime import UTC, date, datetime

    from custom_components.ninebot.assistant_plans import async_detail_proposal

    now = datetime(2026, 11, 7, tzinfo=UTC)
    freezer.move_to(now)
    archive = entry.runtime_data.coordinator.statistics.archive
    await archive.async_record_month("SyntheticSN", proposal_month(), now)
    await hass.async_block_till_done()
    context = Context(user_id="synthetic")
    app_client.reset_mock()
    before = hashlib.sha256(archive.path.read_bytes()).digest()
    with patch.object(hass.auth, "async_get_user", return_value=user()) as lookup:
        result = await async_detail_proposal(
            hass, context, proposal_device, "2026-11-06", "2026-11-06"
        )
        assert all(call.args == ("synthetic",) for call in lookup.call_args_list)
    plan = result.plan
    assert plan is not None
    assert plan.user_id == context.user_id and plan.account_key == archive.owner
    assert plan.sn == "SyntheticSN" and plan.vehicle_ref == proposal_device
    assert plan.start_date == plan.end_date == date(2026, 11, 6)
    assert plan.created_at == now and plan.attempts == 2
    output = result.model_data()
    assert output["coverage"]["known_local_rides"] == 2
    assert output["coverage"]["missing_months"] == []
    assert output["requests"]["maximum_after_approval"] == 2
    assert output["requests"]["account_minimum_interval_seconds"] == 60
    assert output["task_created"] is False and output["approval_required"] is True
    assert output["plan"]["digest"] == plan.digest
    encoded = json.dumps(output)
    for private in ("SyntheticSN", archive.owner, "private-ride-0", "nonce", "password", "token"):
        assert private not in encoded
    assert hashlib.sha256(archive.path.read_bytes()).digest() == before
    for method in (
        app_client.async_list_vehicles,
        app_client.async_get_status,
        app_client.async_get_battery,
        app_client.async_get_travel,
    ):
        method.assert_not_awaited()


@pytest.mark.parametrize("count", [None, 0])
async def test_proposal_no_local_scope_does_not_create_an_executable_plan(
    hass, entry, proposal_device, freezer, count
):
    from datetime import UTC, datetime

    from custom_components.ninebot.assistant_plans import async_detail_proposal

    now = datetime(2026, 11, 7, tzinfo=UTC)
    freezer.move_to(now)
    if count is not None:
        await entry.runtime_data.coordinator.statistics.archive.async_record_month(
            "SyntheticSN", proposal_month(count), now
        )
    with patch.object(hass.auth, "async_get_user", return_value=user()):
        result = await async_detail_proposal(
            hass, Context(user_id="synthetic"), proposal_device, "2026-11-06", "2026-11-06"
        )
    assert result.plan is None
    output = result.model_data()
    assert output["plan"] is None and output["requests"]["maximum_after_approval"] == 0
    assert output["coverage"]["upstream_history_complete"] == "unverified"
    # Setup may have observed this month without a complete list.
    if count == 0:
        assert output["coverage"]["known_local_rides"] == 0
        assert output["coverage"]["months"][0]["observed_list_complete"] is True


@pytest.mark.parametrize("change", ["consent", "generation", "archive"])
async def test_proposal_rejects_changes_during_the_archive_read(
    hass, entry, proposal_device, freezer, change
):
    from datetime import UTC, datetime

    from custom_components.ninebot.assistant_plans import async_detail_proposal

    now = datetime(2026, 11, 7, tzinfo=UTC)
    freezer.move_to(now)
    co = entry.runtime_data.coordinator
    archive = co.statistics.archive
    original = archive.async_detail_preview

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == "consent":
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, "enable_assistant": False}
            )
        elif change == "generation":
            co._generation += 1
        else:
            co.statistics.archive_ready = False
        return result

    with (
        patch.object(hass.auth, "async_get_user", return_value=user()),
        patch.object(archive, "async_detail_preview", side_effect=changed),
        pytest.raises(AssistantError, match="^not_authorized$"),
    ):
        await async_detail_proposal(
            hass, Context(user_id="synthetic"), proposal_device, "2026-11-06", "2026-11-06"
        )


@pytest.mark.parametrize(
    "first,last",
    [("2026/11/06", "2026-11-06"), ("2026-11-06", "2026-11-05"), ("2026-10-01", "2026-11-01")],
)
async def test_proposal_invalid_dates_never_read_archive(hass, entry, proposal_device, first, last):
    from custom_components.ninebot.assistant_plans import async_detail_proposal

    with (
        patch.object(hass.auth, "async_get_user", return_value=user()),
        patch.object(
            entry.runtime_data.coordinator.statistics.archive, "async_detail_preview"
        ) as query,
        pytest.raises(AssistantError, match="^invalid_arguments$"),
    ):
        await async_detail_proposal(
            hass, Context(user_id="synthetic"), proposal_device, first, last
        )
    query.assert_not_awaited()
