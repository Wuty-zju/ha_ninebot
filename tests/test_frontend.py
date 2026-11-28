"""Lifecycle of the read-only archive panel and its packaged assets."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.ninebot.frontend import ARCHIVE_PATH, ARCHIVE_URL, async_update_archive_panel


async def test_concurrent_accounts_share_one_panel_until_last_unload(hass):
    hass.config.components.add("frontend")
    frontend = SimpleNamespace(
        async_panel_exists=Mock(return_value=False), async_remove_panel=Mock()
    )
    panel = SimpleNamespace(async_register_panel=AsyncMock())

    async def imported(_hass, name):
        await asyncio.sleep(0)
        return frontend if name.endswith(".frontend") else panel

    with patch("custom_components.ninebot.frontend.async_import_module", side_effect=imported):
        await asyncio.gather(
            async_update_archive_panel(hass, "a", loaded=True),
            async_update_archive_panel(hass, "b", loaded=True),
        )
        panel.async_register_panel.assert_awaited_once()
        args = panel.async_register_panel.call_args.kwargs
        assert args["frontend_url_path"] == ARCHIVE_PATH
        assert args["module_url"] == ARCHIVE_URL
        assert args["require_admin"] is False

        await async_update_archive_panel(hass, "a", loaded=False)
        frontend.async_remove_panel.assert_not_called()
        await async_update_archive_panel(hass, "b", loaded=False)
        frontend.async_remove_panel.assert_called_once_with(
            hass, ARCHIVE_PATH, warn_if_unknown=False
        )

        await async_update_archive_panel(hass, "b", loaded=True)
        assert panel.async_register_panel.await_count == 2


async def test_headless_setup_and_foreign_panel_are_left_alone(hass):
    frontend = SimpleNamespace(
        async_panel_exists=Mock(return_value=True), async_remove_panel=Mock()
    )
    panel = SimpleNamespace(async_register_panel=AsyncMock())

    with patch(
        "custom_components.ninebot.frontend.async_import_module", side_effect=(frontend, panel)
    ) as imported:
        await async_update_archive_panel(hass, "headless", loaded=True)
        imported.assert_not_called()
        await async_update_archive_panel(hass, "headless", loaded=False)

        hass.config.components.add("frontend")
        await async_update_archive_panel(hass, "collision", loaded=True)
        await async_update_archive_panel(hass, "collision", loaded=False)
        panel.async_register_panel.assert_not_awaited()
        frontend.async_remove_panel.assert_not_called()
