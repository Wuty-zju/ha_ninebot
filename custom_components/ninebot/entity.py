"""Shared entity helpers for Ninebot integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, MANUFACTURER
from .coordinator import NinebotDataUpdateCoordinator


class NinebotCoordinatorEntity(CoordinatorEntity[NinebotDataUpdateCoordinator]):
    """Base entity bound to one Ninebot device SN."""

    _sn: str

    def __init__(self, coordinator: NinebotDataUpdateCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn

    @property
    def _merged(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return {}
        value = data.get(self._sn)
        return value if isinstance(value, dict) else {}

    @property
    def _device(self) -> dict[str, Any]:
        value = self._merged.get("device")
        return value if isinstance(value, dict) else {}

    @property
    def _state(self) -> dict[str, Any]:
        value = self._merged.get("state")
        return value if isinstance(value, dict) else {}

    @property
    def device_info(self) -> DeviceInfo:
        name = str(self._device.get("device_name") or self._sn)
        model = str(self._device.get("model") or "Ninebot Vehicle")
        return DeviceInfo(
            identifiers={(DOMAIN, self._sn)},
            name=name,
            manufacturer=MANUFACTURER,
            model=model,
        )

    def _build_unique_id(self, entity_key: str) -> str:
        return f"ninebot_{self._sn}_{entity_key}".lower()

    def _build_object_id(self, entity_key: str) -> str:
        return f"ninebot_{self._sn}_{entity_key}".lower()
