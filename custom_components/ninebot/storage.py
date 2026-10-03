"""Versioned local SOC models; never imports v1 energy counters or raw state."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .estimation import EnergyModel


class ModelStorage:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store[dict[str, Any]](hass, 1, f"ninebot.{entry_id}.energy_v2", private=True)
        self.models: dict[str, EnergyModel] = {}

    async def async_load(self) -> None:
        raw = await self._store.async_load()
        if (
            isinstance(raw, dict)
            and raw.get("model_version") == 2
            and isinstance(raw.get("models"), dict)
        ):
            self.models = {
                sn: EnergyModel.restore(value)
                for sn, value in raw["models"].items()
                if isinstance(sn, str)
            }
            for model in self.models.values():
                model.reset_baseline()

    def model(self, sn: str) -> EnergyModel:
        return self.models.setdefault(sn, EnergyModel())

    def _data(self) -> dict[str, Any]:
        return {
            "model_version": 2,
            "models": {sn: model.dump() for sn, model in self.models.items()},
        }

    def schedule_save(self) -> None:
        self._store.async_delay_save(self._data, 5)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())
