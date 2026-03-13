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
        return self.coordinator.data.get(self._sn, {})

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
        name = str(self._device.get("deviceName") or self._sn)
        model = str(self._device.get("model") or "Ninebot Vehicle")
        return DeviceInfo(
            identifiers={(DOMAIN, self._sn)},
            name=name,
            manufacturer=MANUFACTURER,
            model=model,
        )
