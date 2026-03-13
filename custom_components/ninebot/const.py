"""Constants for the Ninebot integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from typing import Final

DOMAIN: Final = "ninebot"
PLATFORMS: Final = ["sensor", "lock", "binary_sensor", "image"]

CONF_LANG: Final = "lang"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DEBUG: Final = "debug"

DEFAULT_LANG: Final = "zh"
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
DEFAULT_DEBUG: Final = False

MANUFACTURER: Final = "Ninebot"

LOGIN_BASE_URL: Final = "https://api-passport-bj.ninebot.com"
LOGIN_PATH: Final = "/v3/openClaw/user/login"
DEVICE_BASE_URL: Final = "https://cn-cbu-gateway.ninebot.com"
DEVICES_PATH: Final = "/app-api/inner/device/ai/get-device-list"
DEVICE_DYNAMIC_INFO_PATH: Final = "/app-api/inner/device/ai/get-device-dynamic-info"

DEFAULT_TIMEOUT_SECONDS: Final = 15

DATA_COORDINATOR: Final = "coordinator"

SCAN_INTERVAL_DEFAULT: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

STATUS_LOCKED: Final = 0
STATUS_UNLOCKED: Final = 1

CHARGING_ON: Final = 1
CHARGING_OFF: Final = 0

MAIN_POWER_ON: Final = 1
MAIN_POWER_OFF: Final = 0

SENSOR_KEYS: Final[tuple[str, ...]] = (
	"battery",
	"device_name",
	"sn",
	"vehicle_lock_raw",
	"gsm_csq",
	"gsm_rssi",
	"remaining_range",
	"remaining_charge_time",
	"gsm_report_timestamp",
	"gsm_report_time",
	"location",
)

BINARY_SENSOR_KEYS: Final[tuple[str, ...]] = (
	"vehicle_lock",
	"charging",
	"main_power",
)

IMAGE_KEYS: Final[tuple[str, ...]] = (
	"vehicle_image",
)

LOCK_KEYS: Final[tuple[str, ...]] = (
	"vehicle_lock_control",
)


def status_to_locked(status: int | None) -> bool | None:
	"""Convert Ninebot status to lock state.

	Self-check mapping contract:
	- status=0 -> locked=True
	- status=1 -> locked=False
	"""
	if status == STATUS_LOCKED:
		return True
	if status == STATUS_UNLOCKED:
		return False
	return None


def parse_lock_status_value(value: Any) -> int | None:
	"""Parse lock status from API payload without semantic conversion.

	Accepted raw values:
	- 0: locked
	- 1: unlocked
	"""
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		text = value.strip()
		if text in {"0", "1"}:
			return int(text)
	return None


def lock_status_from_state(state: dict[str, Any]) -> int | None:
	"""Read raw lock status from merged coordinator state."""
	return parse_lock_status_value(state.get("status"))
