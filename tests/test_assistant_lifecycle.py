"""Optional native API lifetime without sessions, cloud traffic or a model."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from custom_components.ninebot.assistant import DATA_KEY, async_update_assistant_api

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def enabled_entry(enabled=True, vehicles=None):
    return SimpleNamespace(
        options={"enable_assistant": enabled, "assistant_vehicles": vehicles or ["synthetic"]},
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(_stopping=False)),
    )


async def test_off_never_imports_adapter_or_creates_registration(hass):
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[enabled_entry(False)]),
        patch("custom_components.ninebot.assistant.async_import_module") as imported,
    ):
        await async_update_assistant_api(hass)
    assert DATA_KEY not in hass.data
    imported.assert_not_called()


async def test_empty_scope_does_not_register(hass):
    entry = enabled_entry()
    entry.options["assistant_vehicles"] = []
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[entry]),
        patch("custom_components.ninebot.assistant.async_import_module") as imported,
    ):
        await async_update_assistant_api(hass)
    assert DATA_KEY not in hass.data
    imported.assert_not_called()


async def test_stopping_or_unloading_account_does_not_register(hass):
    stopping, unloading = enabled_entry(), enabled_entry()
    stopping.runtime_data.coordinator._stopping = True
    unloading.state = ConfigEntryState.UNLOAD_IN_PROGRESS
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[stopping, unloading]),
        patch("custom_components.ninebot.assistant.async_import_module") as imported,
    ):
        await async_update_assistant_api(hass)
    assert DATA_KEY not in hass.data
    imported.assert_not_called()


async def test_concurrent_accounts_share_registration_and_last_disable_revokes(hass):
    entries = [enabled_entry(), enabled_entry()]
    remove = Mock()
    register = Mock(return_value=remove)

    async def import_adapter(*args):
        await asyncio.sleep(0)
        return SimpleNamespace(async_register=register)

    with (
        patch.object(hass.config_entries, "async_entries", side_effect=lambda *a: entries),
        patch("custom_components.ninebot.assistant.native_api_supported", return_value=True),
        patch(
            "custom_components.ninebot.assistant.async_import_module", side_effect=import_adapter
        ),
    ):
        await asyncio.gather(async_update_assistant_api(hass), async_update_assistant_api(hass))
        register.assert_called_once_with(hass)
        entries.pop()
        await async_update_assistant_api(hass)
        remove.assert_not_called()
        entries.clear()
        await async_update_assistant_api(hass)
        remove.assert_called_once_with()
        assert hass.data[DATA_KEY].unregister is None


async def test_legacy_core_does_not_import_adapter_and_warns_once(hass, caplog):
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[enabled_entry()]),
        patch("custom_components.ninebot.assistant.native_api_supported", return_value=False),
        patch("custom_components.ninebot.assistant.async_import_module") as imported,
    ):
        await async_update_assistant_api(hass)
        await async_update_assistant_api(hass)
    imported.assert_not_called()
    assert caplog.text.count("unsupported_native_api") == 1


async def test_api_collision_does_not_expose_exception_or_break_existing_runtime(hass, caplog):
    adapter = SimpleNamespace(async_register=Mock(side_effect=HomeAssistantError("PRIVATE")))
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[enabled_entry()]),
        patch("custom_components.ninebot.assistant.native_api_supported", return_value=True),
        patch(
            "custom_components.ninebot.assistant.async_import_module",
            AsyncMock(return_value=adapter),
        ),
    ):
        await async_update_assistant_api(hass)
    assert hass.data[DATA_KEY].error == "native_api_unavailable"
    assert "PRIVATE" not in caplog.text


async def test_loaded_account_hot_enable_disable_and_unload(hass, entry, app_client):
    """Options do not reload the account or start another polling worker."""
    remove = Mock()
    adapter = SimpleNamespace(async_register=Mock(return_value=remove))
    with (
        patch("custom_components.ninebot.assistant.native_api_supported", return_value=True),
        patch("custom_components.ninebot.assistant.async_import_module", return_value=adapter),
        patch.object(hass.config_entries, "async_reload") as reload_entry,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        runtime = entry.runtime_data
        adapter.async_register.assert_not_called()
        for enabled in (True, False, True):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    "enable_assistant": enabled,
                    "assistant_vehicles": ["SyntheticSN"],
                },
            )
            await hass.async_block_till_done()
            assert entry.runtime_data is runtime
        assert adapter.async_register.call_count == 2
        assert remove.call_count == 1
        reload_entry.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert remove.call_count == 2
        assert hass.data[DATA_KEY].unregister is None
