"""Constant variables used by integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "tion"
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]
DEFAULT_AUTH_FILENAME = "tion_auth"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=1)
MAGICAIR_DEVICE = "magicair"
BREEZER_DEVICE = "breezer"
CO2_PPM = "ppm"
HUM_PERCENT = "%"
SWING_INSIDE = "Inside"
SWING_OUTSIDE = "Outside"
SWING_MIXED = "Mixed"
LAST_FAN_SPEED_SYNCED = "last_fan_speed_synced"
MODELS = {
    "co2mb": "MagicAir",
    "co2Plus": "Module CO2+",
    "tionO2Rf": "Breezer O2",
    "tionClever": "Clever",
    "breezer3": "Breezer 3S",
    "breezer4": "Breezer 4S",
}
