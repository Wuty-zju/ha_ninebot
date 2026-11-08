"""Optional native panel lifetime; no browser, account or cloud connection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.ninebot.assistant import DATA_KEY, async_update_assistant_api
from custom_components.ninebot.assistant_panel import PANEL_PATH, PANEL_URL, async_register


async def test_headless_review_does_not_import_frontend(hass):
    with patch("custom_components.ninebot.assistant_panel.async_import_module") as imported:
        assert await async_register(hass) is None
    imported.assert_not_called()


async def test_panel_uses_packaged_assets_admin_access_and_never_overwrites_a_path(hass):
    frontend = SimpleNamespace(
        async_panel_exists=Mock(return_value=False), async_remove_panel=Mock()
    )
    panel = SimpleNamespace(async_register_panel=AsyncMock())
    hass.config.components.add("frontend")

    async def imported(_hass, name):
        return frontend if name.endswith(".frontend") else panel

    with (
        patch(
            "custom_components.ninebot.assistant_panel.async_import_module", side_effect=imported
        ),
        patch(
            "custom_components.ninebot.assistant_panel.async_register_frontend", new=AsyncMock()
        ) as assets,
    ):
        remove = await async_register(hass)
        assets.assert_awaited_once_with(hass)
        args = panel.async_register_panel.call_args.kwargs
        assert args["frontend_url_path"] == PANEL_PATH
        assert args["module_url"] == PANEL_URL and args["require_admin"] is True
        assert args["embed_iframe"] is False and "config_panel_domain" not in args
        remove()
        frontend.async_remove_panel.assert_called_once_with(hass, PANEL_PATH, warn_if_unknown=False)
        frontend.async_panel_exists.return_value = True
        import pytest

        with pytest.raises(ValueError):
            await async_register(hass)
        assert panel.async_register_panel.await_count == 1


async def test_concurrent_accounts_share_panel_and_last_task_opt_out_removes_it(hass):
    from homeassistant.config_entries import ConfigEntryState

    entry = SimpleNamespace(
        options={
            "enable_assistant": True,
            "enable_assistant_tasks": True,
            "assistant_vehicles": ["synthetic"],
        },
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(_stopping=False)),
    )
    remove_panel, remove_read, remove_tasks = Mock(), Mock(), Mock()
    panel = AsyncMock(return_value=remove_panel)

    async def imported(_hass, name):
        if name.endswith("assistant_panel"):
            return SimpleNamespace(async_register=panel)
        if name.endswith("assistant_review"):
            return SimpleNamespace(async_register=Mock())
        return SimpleNamespace(
            async_register=Mock(
                return_value=remove_tasks if name.endswith("assistant_task_api") else remove_read
            )
        )

    hass.config.components.add("frontend")
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[entry]),
        patch("custom_components.ninebot.assistant.native_api_supported", return_value=True),
        patch("custom_components.ninebot.assistant.async_import_module", side_effect=imported),
    ):
        await asyncio.gather(async_update_assistant_api(hass), async_update_assistant_api(hass))
        panel.assert_awaited_once()
        entry.options["enable_assistant_tasks"] = False
        await async_update_assistant_api(hass)
        remove_panel.assert_called_once()
        remove_tasks.assert_called_once()
        remove_read.assert_not_called()
        assert hass.data[DATA_KEY].unregister_panel is None


async def test_panel_collision_is_optional_and_never_logs_exception_payload(hass, caplog):
    from custom_components.ninebot.assistant import AssistantRegistration, _async_update_panel

    hass.config.components.add("frontend")
    state = AssistantRegistration()
    with patch(
        "custom_components.ninebot.assistant.async_import_module",
        return_value=SimpleNamespace(
            async_register=AsyncMock(side_effect=ValueError("PRIVATE PATH"))
        ),
    ):
        await _async_update_panel(hass, state, True)
        await _async_update_panel(hass, state, True)
    assert state.panel_error == "panel_unavailable" and state.error is None
    assert caplog.text.count("review panel is unavailable") == 1
    assert "PRIVATE PATH" not in caplog.text


async def test_task_option_defaults_off_and_preserves_an_explicit_choice(hass, entry, app_client):
    with (
        patch("custom_components.ninebot.async_update_assistant_api", new=AsyncMock()),
        patch("custom_components.ninebot.config_flow.native_api_supported", return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for enabled in (False, True):
            if enabled:
                hass.config_entries.async_update_entry(
                    entry,
                    options={
                        **entry.options,
                        "enable_assistant": True,
                        "assistant_vehicles": ["SyntheticSN"],
                        "enable_assistant_tasks": True,
                    },
                )
                await hass.async_block_till_done()
            form = await hass.config_entries.options.async_init(entry.entry_id)
            field = next(
                key for key in form["data_schema"].schema if key == "enable_assistant_tasks"
            )
            assert field.default() is enabled
            assert list(form["data_schema"].schema)[-1] == "debug_mode"
