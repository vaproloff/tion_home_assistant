"""The Tion component."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_FILE_PATH,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import Config, HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import BREEZER_DEVICE, DOMAIN, MAGICAIR_DEVICE, PLATFORMS
from .tion_api import Breezer, MagicAir, TionApi

_LOGGER = logging.getLogger(__name__)


def create_api(user, password, interval, auth_fname):
    """Return Tion Api."""
    return TionApi(
        user, password, min_update_interval_sec=interval, auth_fname=auth_fname
    )


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up this integration with the UI. YAML is not supported."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    tion_api = await hass.async_add_executor_job(
        create_api,
        entry.data.get(CONF_USERNAME),
        entry.data.get(CONF_PASSWORD),
        entry.data.get(CONF_SCAN_INTERVAL),
        entry.data.get(CONF_FILE_PATH),
    )

    assert tion_api.authorization, "Couldn't get authorisation data!"
    _LOGGER.info("Api initialized with authorization %s", tion_api.authorization)

    hass.data[DOMAIN][entry.entry_id] = tion_api

    device_registry = dr.async_get(hass)

    devices = await hass.async_add_executor_job(tion_api.get_devices)
    models = {
        "co2mb": "MagicAir",
        "co2Plus": "Модуль CO2+",
        "tionO2Rf": "Бризер O2",
        "tionClever": "Clever",
        "breezer3": "Бризер 3S",
        "breezer4": "Бризер 4S",
    }
    device: Breezer
    for device in devices:
        _LOGGER.info("Device type: %s", device.type)
        if device.valid:
            device_type = (
                BREEZER_DEVICE
                if type(device) == Breezer
                else (MAGICAIR_DEVICE if type(device) == MagicAir else None)
            )
            if device_type:
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, device.guid)},
                    manufacturer="TION",
                    model=models.get(device.type, "Unknown device"),
                    name=device.name,
                )
            else:
                _LOGGER.info("Unused device: %s", device)
        else:
            _LOGGER.info("Skipped device %s, because of 'valid' property", device)

    await hass.async_create_task(
        hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    )

    return True
