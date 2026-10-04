"""Versioned local SOC models; never imports v1 energy counters or raw state."""

from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .estimation import EnergyModel


class ModelStorage:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store[dict[str, Any]](hass, 1, f"ninebot.{entry_id}.energy_v2", private=True)
        self.models: dict[str, EnergyModel] = {}
        self._hass = hass
        self._issue_id = f"model_storage_{entry_id}"
        self._writable = False

    async def async_load(self) -> None:
        self._writable = False
        raw = await self._store.async_load()
        if raw is not None:
            if not (
                isinstance(raw, dict)
                and type(raw.get("model_version")) is int
                and raw["model_version"] == 2
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
                raise ConfigEntryError(
                    translation_domain=DOMAIN, translation_key="model_storage_invalid"
                )
            self.models = {
                sn: EnergyModel.restore(value)
                for sn, value in raw["models"].items()
                if isinstance(sn, str)
            }
            for model in self.models.values():
                model.reset_baseline()
        else:
            self.models = {}
        self._writable = True
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)

    def model(self, sn: str) -> EnergyModel:
        return self.models.setdefault(sn, EnergyModel())

    def configure(self, sn: str, values: dict[str, float]) -> None:
        """Options and Number share one atomic in-memory configuration commit.

        Validate on a copy before replacing the model. A batch voltage/capacity
        change creates one generation, never half-applied configuration. The
        existing HA Store owns persistence and unload flushes scheduled saves.
        """
        old = self.model(sn)
        updated = deepcopy(old)
        for key, value in values.items():
            updated.configure(key, value)
        if updated.generation != old.generation:
            updated.generation = old.generation + 1
        self.models[sn] = updated
        self.schedule_save()

    def _data(self) -> dict[str, Any]:
        return {
            "model_version": 2,
            "models": {sn: model.dump() for sn, model in self.models.items()},
        }

    def schedule_save(self) -> None:
        if self._writable:
            self._store.async_delay_save(self._data, 5)

    async def async_save(self) -> None:
        if self._writable:
            await self._store.async_save(self._data())
