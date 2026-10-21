"""Rated parameters only; legacy counters are deliberately not migrated."""

import asyncio
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import json as json_util
from homeassistant.util.file import WriteError

from .async_utils import async_drain
from .battery_parameters import BatteryParameters
from .const import DOMAIN
from .parsing import number


class ParameterStorage(Store[dict[str, Any]]):
    """Acknowledge native delayed/final writes without losing a failed save."""

    def __init__(
        self, hass: HomeAssistant, key: str, written: Callable[[dict | None], None]
    ) -> None:
        super().__init__(hass, 1, key, private=True)
        self._written = written

    async def _async_write_data(self, data: dict) -> None:
        try:
            await super()._async_write_data(data)
        except (OSError, WriteError, json_util.SerializationError):
            self._written(None)
        else:
            self._written(data["data"])


class ModelStorage:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = ParameterStorage(hass, f"ninebot.{entry_id}.energy_v2", self._written)
        self.models: dict[str, BatteryParameters] = {}
        self._hass = hass
        self._issue_id = f"model_storage_{entry_id}"
        self._writable = False
        self._dirty = False
        self._save_lock = asyncio.Lock()

    def _written(self, data: dict | None) -> None:
        issue = f"{self._issue_id}_write"
        if data is None:
            self._dirty = True
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="model_storage_write",
            )
        elif data == self._data():
            self._dirty = False
            ir.async_delete_issue(self._hass, DOMAIN, issue)

    async def async_load(self) -> None:
        self._writable = False
        self._dirty = False
        raw = await self._store.async_load()
        if raw is not None:
            if not (
                isinstance(raw, dict)
                and type(raw.get("model_version")) is int
                and raw["model_version"] in (2, 3)
                and isinstance(raw.get("models"), dict)
            ):
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    self._issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="model_storage_invalid",
                )
                # Optional local specifications must not block cloud telemetry.
                # Keep this file read-only until the owner resolves the Repair.
                self.models = {}
                return
            self.models = {
                sn: BatteryParameters.restore(value)
                for sn, value in raw["models"].items()
                if isinstance(sn, str)
            }
        else:
            self.models = {}
        self._dirty = isinstance(raw, dict) and raw.get("model_version") == 2
        self._writable = True
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)

    @property
    def writable(self) -> bool:
        return self._writable

    def model(self, sn: str) -> BatteryParameters:
        if sn not in self.models:
            self.models[sn] = BatteryParameters()
        return self.models[sn]

    def configure(self, sn: str, values: dict[str, float | None]) -> None:
        """Validate the entire batch before changing or scheduling anything."""
        if not self._writable:
            raise ValueError("Parameter storage is not writable")
        old = self.model(sn)
        fields = old.dump()
        for key, value in values.items():
            maximum = {"voltage": 300, "capacity": 500}.get(key)
            if maximum is None:
                raise ValueError("Invalid battery parameter")
            valid = None if value is None else number(value, 1, maximum)
            if value is not None and valid is None:
                raise ValueError("Invalid battery parameter")
            fields[key] = valid
        updated = BatteryParameters(**fields)
        if updated == old:
            return
        self.models[sn] = updated
        self._dirty = True
        self.schedule_save()

    def _data(self) -> dict[str, Any]:
        return {
            "model_version": 3,
            "models": {sn: model.dump() for sn, model in self.models.items()},
        }

    def schedule_save(self) -> None:
        if self._writable and self._dirty:
            self._store.async_delay_save(self._data, 5)

    async def async_save(self) -> bool:
        async with self._save_lock:
            if self._writable and self._dirty:
                await async_drain(asyncio.create_task(self._store.async_save(self._data())))
            return self._writable and not self._dirty

    async def async_remove(self) -> None:
        """Discard entry-owned specifications, including a pending delayed save."""
        async with self._save_lock:
            self._writable = False
            self._dirty = False
            await self._store.async_remove()
