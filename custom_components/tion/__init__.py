"""The Tion component."""

import logging

import voluptuous as vol

from homeassistant.const import (
    CONF_FILE_PATH,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv

from .const import (
    BREEZER_DEVICE,
    DEFAULT_AUTH_FILENAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAGICAIR_DEVICE,
    TION_API,
)
from .tion_api import Breezer, MagicAir, TionApi

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): cv.time_period,
                vol.Optional(CONF_FILE_PATH, default=DEFAULT_AUTH_FILENAME): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def create_api(user, password, interval, auth_fname):
    """Return Tion Api."""
    return TionApi(
        user, password, min_update_interval_sec=interval, auth_fname=auth_fname
    )


async def async_setup(hass: HomeAssistant, config):
    """Set up Tion Component."""
    api = await hass.async_add_executor_job(
        create_api,
        config[DOMAIN][CONF_USERNAME],
        config[DOMAIN][CONF_PASSWORD],
        (config[DOMAIN][CONF_SCAN_INTERVAL]).seconds,
        hass.config.path(config[DOMAIN][CONF_FILE_PATH]),
    )

    assert api.authorization, "Couldn't get authorisation data!"
    _LOGGER.info("Api initialized with authorization %s", api.authorization)

    hass.data[TION_API] = api

    discovery_info = {}
    devices = await hass.async_add_executor_job(api.get_devices)
    device: Breezer | MagicAir
    for device in devices:
        if device.valid:
            device_type = (
                BREEZER_DEVICE
                if type(device) == Breezer
                else (MAGICAIR_DEVICE if type(device) == MagicAir else None)
            )
            if device_type:
                if "sensor" not in discovery_info:
                    discovery_info["sensor"] = []
                discovery_info["sensor"].append(
                    {"type": device_type, "guid": device.guid}
                )
                if device_type == BREEZER_DEVICE:
                    if "climate" not in discovery_info:
                        discovery_info["climate"] = []
                    discovery_info["climate"].append(
                        {"type": device_type, "guid": device.guid}
                    )
            else:
                _LOGGER.info("Unused device %s", device)
        else:
            _LOGGER.info("Skipped device %s, because of 'valid' property", device)

    for device_type, devices in discovery_info.items():
        await discovery.async_load_platform(hass, device_type, DOMAIN, devices, config)
        _LOGGER.info("Found %s %s devices", len(devices), device_type)

    # Return boolean to indicate that initialization was successful.
    return True
