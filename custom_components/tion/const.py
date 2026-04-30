"""Constant variables used by integration."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "tion"
DEFAULT_SCAN_INTERVAL = 60
AUTH_DATA = "auth"
MANUFACTURER = "Tion"
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
