"""Constant variables used by integration."""

from datetime import timedelta
from enum import StrEnum

from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_SLEEP,
)
from homeassistant.const import Platform

DOMAIN = "tion"
# v4 pushes no online/offline events: only re-reading the structure shows them.
REFRESH_INTERVAL = timedelta(seconds=60)
# Seconds entities stay available after the live channel drops.
DISCONNECT_GRACE = 60
# HA's unit enums for these arrive in 2026.7, above our minimum version.
UNIT_PPM = "ppm"
UNIT_UG_PER_M3 = "μg/m³"
DEFAULT_TARGET_CO2 = 800
DEFAULT_PID_BASE_OUTPUT = 20.0
DEFAULT_PID_KP = 0.5
DEFAULT_PID_KI = 0.001
DEFAULT_PID_KD = 0.0
DEFAULT_PID_INTERVAL = 60
PID_INTERVAL_RANGE = (10, 600)
DEFAULT_PID_MIN_SPEED = 1
FAN_LOCAL_PID = "local_pid"
# unique_id keys of a breezer's local PID numbers.
PID_NUMBER_KEYS = ("min_speed_set", "max_speed_set", "external_target_co2")
CONF_CAPTCHA_TOKEN = "captcha_token"
CONF_DEVICE_KEY = "device_key"
CONF_DEVICE_KEY_ID = "device_key_id"
MANUFACTURER = "Tion"
CONF_BREEZER_GUID = "breezer_guid"
CONF_CO2_SENSOR_ENTITY_ID = "co2_sensor_entity_id"
CONF_PID_BREEZERS = "pid_breezers"
CONF_PID_ENABLED = "pid_enabled"
CONF_PID_BASE_OUTPUT = "pid_base_output"
CONF_PID_KP = "pid_kp"
CONF_PID_KI = "pid_ki"
CONF_PID_KD = "pid_kd"
CONF_PID_INTERVAL = "pid_interval"
CONF_PID_MIN_SPEED = "pid_min_speed"
CONF_PID_MAX_SPEED = "pid_max_speed"
CONF_PID_TARGET_CO2 = "pid_target_co2"
CONF_PRESETS = "presets"
CONF_PRESET_MIN_SPEED = "min_speed"
CONF_PRESET_MAX_SPEED = "max_speed"
CONF_PRESET_TYPE = "type"
CONF_PRESET_SPEED = "speed"

SUPPORTED_PRESETS: tuple[str, ...] = (
    PRESET_ECO,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_SLEEP,
    PRESET_ACTIVITY,
    PRESET_HOME,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

MODEL_NAMES = {
    "b4s_ble": "Breezer 4S",
    "br3s_rf": "Breezer 3S",
    "o2_rf": "Breezer O2",
    "bs3xx_rf": "MagicAir",
    "bs4xx_rf": "MagicAir",
    "thco2_rf": "Module CO2+",
}


class SwingMode(StrEnum):
    """Supported swing modes."""

    SWING_INSIDE = "inside"
    SWING_OUTSIDE = "outside"
    SWING_MIXED = "mixed"


class TionPresetType(StrEnum):
    """Supported preset types."""

    MANUAL = "manual"
    LOCAL_PID = "local_pid"


class PidStatus(StrEnum):
    """What the local PID of a breezer is doing."""

    INACTIVE = "inactive"
    RUNNING = "running"
    PAUSED_DEVICE_UNAVAILABLE = "paused_device_unavailable"
    PAUSED_INVALID_DEVICE_DATA = "paused_invalid_device_data"
    PAUSED_SENSOR_UNAVAILABLE = "paused_sensor_unavailable"
    SEND_FAILED = "send_failed"
