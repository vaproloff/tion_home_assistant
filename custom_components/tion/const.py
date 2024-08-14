"""Constant variables used by integration."""

from datetime import timedelta
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "tion"
DEFAULT_AUTH_FILENAME = "tion_auth"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=1)
MANUFACTURER = "Tion"
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]


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
    """Component supported device types."""

    SWING_INSIDE = "Inside"
    SWING_OUTSIDE = "Outside"
    SWING_MIXED = "Mixed"
