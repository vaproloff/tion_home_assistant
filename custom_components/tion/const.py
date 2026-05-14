"""Constant variables used by integration."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "tion"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_TARGET_CO2 = 800
DEFAULT_PID_KP = 0.5
DEFAULT_PID_KI = 0.001
DEFAULT_PID_KD = 0.0
AUTH_DATA = "auth"
MANUFACTURER = "Tion"
CONF_BREEZER_GUID = "breezer_guid"
CONF_CO2_SENSOR_ENTITY_ID = "co2_sensor_entity_id"
CONF_PID_BREEZERS = "pid_breezers"
CONF_PID_ENABLED = "pid_enabled"
CONF_PID_KP = "pid_kp"
CONF_PID_KI = "pid_ki"
CONF_PID_KD = "pid_kd"
PID_STATUS_INACTIVE = "inactive"
PID_STATUS_RUNNING = "running"
PID_STATUS_NOT_CONFIGURED = "not_configured"
PID_STATUS_PAUSED_SENSOR_UNAVAILABLE = "paused_sensor_unavailable"
PID_STATUS_PAUSED_DEVICE_UNAVAILABLE = "paused_device_unavailable"
PID_STATUS_PAUSED_INVALID_DEVICE_DATA = "paused_invalid_device_data"
PID_STATUS_SEND_FAILED = "send_failed"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


class TionDeviceType(StrEnum):
    """Component supported device types."""

    BREEZER_O2 = "tionO2Rf"
    BREEZER_3S = "breezer3"
    BREEZER_4S = "breezer4"
    CLEVER = "tionClever"
    MAGIC_AIR = "co2mb"
    MODULE_CO2 = "co2Plus"


BREEZER_TYPES = (
    TionDeviceType.BREEZER_O2,
    TionDeviceType.BREEZER_3S,
    TionDeviceType.BREEZER_4S,
)

MODELS_SUPPORTED: dict[TionDeviceType, str] = {
    TionDeviceType.BREEZER_O2: "Breezer O2",
    TionDeviceType.BREEZER_3S: "Breezer 3S",
    TionDeviceType.BREEZER_4S: "Breezer 4S",
    TionDeviceType.MAGIC_AIR: "MagicAir",
    TionDeviceType.MODULE_CO2: "Module CO2+",
}


class SwingMode(StrEnum):
    """Supported swing modes."""

    SWING_INSIDE = "inside"
    SWING_OUTSIDE = "outside"
    SWING_MIXED = "mixed"


class ZoneMode(StrEnum):
    """Supported zone modes."""

    MANUAL = "manual"
    AUTO = "auto"


class Heater(StrEnum):
    """Breezer 4S heater modes."""

    OFF = "maintenance"
    ON = "heat"
