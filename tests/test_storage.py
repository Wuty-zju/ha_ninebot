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
