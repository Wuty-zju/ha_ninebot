"""Integration constants; polling intervals are policy, not API rate limits."""

from homeassistant.const import Platform

DOMAIN = "ninebot"
VERSION = "2.0.0b7"
PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.IMAGE,
    Platform.LOCK,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.EVENT,
]
CONF_BUSINESS_UID = "business_uid"
CONF_SESSION_KEY = "session_key"
CONF_IDENTITY_SCHEME = "identity_scheme"
CONF_ACCOUNT = "account"
CONF_POLL_INTERVAL = "poll_interval"
CONF_CONTROLS = "enable_controls"
CONF_CONTROL_VEHICLES = "control_vehicles"
CONF_ESTIMATION = "enable_estimation"
CONF_COORDINATES = "enable_coordinates"
DEFAULT_POLL_INTERVAL = 120
DETAIL_INTERVAL = 600
VEHICLE_INTERVAL = 3600
BUSINESS_TIMEZONE = "Asia/Shanghai"
SESSION_DIRECTORY = "ninebot_v2"
CLI_TIMEOUT = 30
MAX_RESPONSE_BYTES = 1024 * 1024
MANUFACTURER = "Ninebot"
