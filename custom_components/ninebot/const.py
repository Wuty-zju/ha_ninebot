"""Constants for the Ninebot integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from typing import Final

DOMAIN: Final = "ninebot"
PLATFORMS: Final = ["sensor", "lock", "binary_sensor", "image", "number", "button"]

CONF_LANG: Final = "lang"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DEFAULT_SCAN_INTERVAL: Final = "default_scan_interval"
CONF_UNLOCKED_SCAN_INTERVAL: Final = "unlocked_scan_interval"
CONF_CHARGING_SCAN_INTERVAL: Final = "charging_scan_interval"
CONF_DISCHARGE_POWER_WINDOW_SECONDS: Final = "discharge_power_window_seconds"
CONF_CHARGE_POWER_WINDOW_SECONDS: Final = "charge_power_window_seconds"
CONF_TOKEN_REFRESH_INTERVAL_HOURS: Final = "token_refresh_interval_hours"
CONF_DEVICE_LIST_REFRESH_INTERVAL_HOURS: Final = "device_list_refresh_interval_hours"
CONF_MAX_DEVICE_INFO_CONCURRENCY: Final = "max_device_info_concurrency"
CONF_DEVICE_INFO_FAILURE_TOLERANCE: Final = "device_info_failure_tolerance"
CONF_DEBUG: Final = "debug"

DEFAULT_LANG: Final = "zh"
DEFAULT_SCAN_INTERVAL: Final = 60
DEFAULT_UNLOCKED_SCAN_INTERVAL: Final = 3
DEFAULT_CHARGING_SCAN_INTERVAL: Final = 30
DEFAULT_DISCHARGE_POWER_WINDOW_SECONDS: Final = 15
DEFAULT_CHARGE_POWER_WINDOW_SECONDS: Final = 30
DEFAULT_TOKEN_REFRESH_INTERVAL_HOURS: Final = 24
DEFAULT_DEVICE_LIST_REFRESH_INTERVAL_HOURS: Final = 24
DEFAULT_MAX_DEVICE_INFO_CONCURRENCY: Final = 3
DEFAULT_DEVICE_INFO_FAILURE_TOLERANCE: Final = 3
COORDINATOR_TICK_SECONDS: Final = 2
MIN_SCAN_INTERVAL: Final = 1
MIN_POWER_WINDOW_SECONDS: Final = 1
MAX_POWER_WINDOW_SECONDS: Final = 600
MIN_REFRESH_INTERVAL_HOURS: Final = 1
MIN_DEVICE_INFO_CONCURRENCY: Final = 1
MAX_DEVICE_INFO_CONCURRENCY: Final = 10
MIN_DEVICE_INFO_FAILURE_TOLERANCE: Final = 0
MAX_DEVICE_INFO_FAILURE_TOLERANCE: Final = 20
DEFAULT_DEBUG: Final = False

DEFAULT_MAIN_BATTERY_VOLTAGE: Final = 72.0
DEFAULT_BATTERY_CAPACITY: Final = 20.0

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
	"battery_calculated",
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
	"battery_nominal_energy",
	"battery_energy_delta",
	"battery_outflow_energy_step",
	"battery_inflow_energy_step",
	"battery_outflow_power",
	"battery_inflow_power",
	"battery_outflow_energy_daily",
	"battery_outflow_energy_monthly",
	"battery_outflow_energy_total",
	"battery_inflow_energy_daily",
	"battery_inflow_energy_monthly",
	"battery_inflow_energy_total",
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

NUMBER_KEYS: Final[tuple[str, ...]] = (
	"main_battery_voltage",
	"battery_capacity",
	"battery_max_range",
)

BUTTON_KEYS: Final[tuple[str, ...]] = (
	"info",
)


def status_to_locked(status: int | None) -> bool | None:
	"""Convert Ninebot status to lock state.

	Self-check mapping contract:
	- status=0 -> locked=True
	- status=1 -> locked=False
	"""
	if status == 0:
		return True
	if status == 1:
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
