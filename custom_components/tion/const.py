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
DEFAULT_TARGET_CO2 = 800
DEFAULT_PID_BASE_OUTPUT = 20.0
DEFAULT_PID_KP = 0.5
DEFAULT_PID_KI = 0.001
DEFAULT_PID_KD = 0.0
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

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

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

    AUTO = "auto"
    MANUAL = "manual"
