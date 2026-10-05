"""Rated parameters only; legacy counters are deliberately not migrated."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .battery_parameters import BatteryParameters
from .const import DOMAIN
from .parsing import number


class ModelStorage:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store[dict[str, Any]](hass, 1, f"ninebot.{entry_id}.energy_v2", private=True)
        self.models: dict[str, BatteryParameters] = {}
        self._hass = hass
        self._issue_id = f"model_storage_{entry_id}"
        self._writable = False
        self._dirty = False

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

    def configure(self, sn: str, values: dict[str, float]) -> None:
        """Validate the entire batch before changing or scheduling anything."""
        if not self._writable:
            raise ValueError("Parameter storage is not writable")
        old = self.model(sn)
        fields = old.dump()
        for key, value in values.items():
            maximum = {"voltage": 300, "capacity": 500}.get(key)
            if maximum is None or (valid := number(value, 1, maximum)) is None:
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

    async def async_save(self) -> None:
        if self._writable and self._dirty:
            await self._store.async_save(self._data())
            self._dirty = False
