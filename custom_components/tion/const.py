"""Constant variables used by integration."""

from datetime import timedelta
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "tion"
DEFAULT_AUTH_FILENAME = "tion_auth"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=1)
AUTH_DATA = "auth"
MANUFACTURER = "Tion"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

SRVC_CONF_TARGET_CO2 = "target_co2"
SRVC_CONF_MIN_SPEED = "min_speed"
SRVC_CONF_MAX_SPEED = "max_speed"


class TionDeviceType(StrEnum):
    """Component supported device types."""

    BREEZER_O2 = "tionO2Rf"
    BREEZER_3S = "breezer3"
    BREEZER_4S = "breezer4"
    CLEVER = "tionClever"
    MAGIC_AIR = "co2mb"
    MODULE_CO2 = "co2Plus"


MODELS_SUPPORTED: dict[TionDeviceType, str] = {
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
