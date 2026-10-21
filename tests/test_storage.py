from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.ninebot.storage import ModelStorage


@pytest.mark.parametrize(
    "raw", [{"model_version": 4, "models": {}}, {"model_version": 2, "models": []}, []]
)
async def test_unknown_storage_is_not_overwritten_and_repair_clears_on_recovery(hass, raw):
    store = ModelStorage(hass, "synthetic-entry")
    store._store.async_load = AsyncMock(return_value=raw)
    store._store.async_save = AsyncMock()
    store._store.async_delay_save = MagicMock()
    store._store.async_load.return_value = {"model_version": 2, "models": {}}
    await store.async_load()
    store._store.async_load.return_value = raw
    await store.async_load()
    assert ir.async_get(hass).async_get_issue("ninebot", "model_storage_synthetic-entry")
    with pytest.raises(ValueError):
        store.configure("synthetic-car", {"voltage": 72})
    store.schedule_save()
    await store.async_save()
    store._store.async_save.assert_not_awaited()
    store._store.async_delay_save.assert_not_called()
    store._store.async_load.return_value = {"model_version": 2, "models": {}}
    await store.async_load()
    assert not ir.async_get(hass).async_get_issue("ninebot", "model_storage_synthetic-entry")
    assert not store.models
    await store.async_save()
    store._store.async_save.assert_awaited_once_with({"model_version": 3, "models": {}})


async def test_failed_parameter_write_retains_dirty_state_and_recovers(hass, tmp_path):
    from unittest.mock import patch

    from homeassistant.helpers.storage import Store
    from homeassistant.util.file import WriteError

    hass.config.config_dir = str(tmp_path)
    store = ModelStorage(hass, "synthetic-write")
    await store.async_load()
    store.configure("car", {"voltage": 72, "capacity": 38})
    with patch.object(Store, "_async_write_data", side_effect=WriteError("synthetic")):
        assert not await store.async_save()
    assert store._dirty and store.writable
    issue = "model_storage_synthetic-write_write"
    assert ir.async_get(hass).async_get_issue("ninebot", issue)
    assert await store.async_save()
    assert not store._dirty
    assert not ir.async_get(hass).async_get_issue("ninebot", issue)
    restored = ModelStorage(hass, "synthetic-write")
    await restored.async_load()
    assert restored.model("car").voltage == 72
    store.configure("car", {"voltage": None, "capacity": None})
    assert await store.async_save()
    await restored.async_load()
    assert restored.model("car").voltage is None
    assert restored.model("car").capacity is None


async def test_delayed_parameter_write_reports_failure_without_losing_retry(
    hass, tmp_path, freezer
):
    from datetime import timedelta
    from unittest.mock import patch

    from homeassistant.helpers.storage import Store
    from homeassistant.util import dt as dt_util
    from homeassistant.util.file import WriteError
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    hass.config.config_dir = str(tmp_path)
    store = ModelStorage(hass, "synthetic-delay")
    await store.async_load()
    store.configure("car", {"voltage": 72})
    with patch.object(Store, "_async_write_data", side_effect=WriteError("synthetic")):
        freezer.tick(timedelta(seconds=6))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert store._dirty
    assert ir.async_get(hass).async_get_issue("ninebot", "model_storage_synthetic-delay_write")
    assert await store.async_save()


async def test_remove_waits_for_native_delayed_write_and_does_not_recreate_file(hass, tmp_path):
    import asyncio
    from pathlib import Path
    from unittest.mock import patch

    from homeassistant.helpers.storage import Store

    hass.config.config_dir = str(tmp_path)
    store = ModelStorage(hass, "synthetic-remove")
    await store.async_load()
    store.configure("car", {"voltage": 72})
    entered, release = asyncio.Event(), asyncio.Event()
    original = Store._async_write_data

    async def delayed_write(native_store, data):
        entered.set()
        await release.wait()
        await original(native_store, data)

    with patch.object(Store, "_async_write_data", delayed_write):
        write = asyncio.create_task(store._store._async_callback_delayed_write())
        await entered.wait()
        removal = asyncio.create_task(store.async_remove())
        try:
            await asyncio.sleep(0)
            assert not removal.done()
        finally:
            release.set()
            await write
            await removal
    assert not await hass.async_add_executor_job(Path(store._store.path).exists)
    assert not store.writable
