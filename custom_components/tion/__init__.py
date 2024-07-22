"""The Tion component."""

import logging

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_FILE_PATH,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, Config, HomeAssistant
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

from .const import (
    BREEZER_DEVICE,
    DEFAULT_AUTH_FILENAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAGICAIR_DEVICE,
    MODELS,
    PLATFORMS,
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


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up integration with the YAML. Not supported."""
    if config.get(DOMAIN):
        tion_entries: list[ConfigEntry] = hass.config_entries.async_entries(DOMAIN)
        for entry in tion_entries:
            if entry.title == config[DOMAIN].get(CONF_USERNAME):
                _LOGGER.debug("Config entry already exists: %s", entry.title)
                async_create_issue(
                    hass,
                    HOMEASSISTANT_DOMAIN,
                    f"deprecated_yaml_{DOMAIN}",
                    breaks_in_ha_version="2025.1.0",
                    is_fixable=False,
                    issue_domain=DOMAIN,
                    severity=IssueSeverity.WARNING,
                    translation_key="deprecated_yaml",
                    translation_placeholders={
                        "domain": DOMAIN,
                        "integration_title": "Tion",
                    },
                )
                return True

        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=config[DOMAIN],
            ),
        )
        async_create_issue(
            hass,
            HOMEASSISTANT_DOMAIN,
            f"deprecated_yaml_{DOMAIN}",
            breaks_in_ha_version="2025.1.0",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=IssueSeverity.WARNING,
            translation_key="deprecated_yaml",
            translation_placeholders={
                "domain": DOMAIN,
                "integration_title": "Tion",
            },
        )

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

    devices: list[MagicAir | Breezer] = await hass.async_add_executor_job(
        tion_api.get_devices
    )
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
                    model=MODELS.get(device.type, "Unknown device"),
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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
