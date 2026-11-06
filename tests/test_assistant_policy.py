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
