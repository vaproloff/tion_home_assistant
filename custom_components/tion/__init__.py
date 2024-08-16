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
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

from .client import TionClient
from .const import (
    AUTH_DATA,
    DEFAULT_AUTH_FILENAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MODELS_SUPPORTED,
    PLATFORMS,
)

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


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up integration with the YAML. Deprecated."""
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
                        "integration_title": MANUFACTURER,
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
                "integration_title": MANUFACTURER,
            },
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    async def update_auth_data(**kwargs):
        hass.config_entries.async_update_entry(entry, data=kwargs)

    session = async_create_clientsession(hass)
    client = TionClient(
        session,
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        min_update_interval_sec=entry.data.get(CONF_SCAN_INTERVAL),
        auth=entry.data.get(AUTH_DATA),
    )
    client.add_update_listener(update_auth_data)

    assert client.authorization, "Couldn't get authorisation data!"
    _LOGGER.info("Api initialized with authorization %s", client.authorization)

    hass.data[DOMAIN][entry.entry_id] = client

    device_registry = dr.async_get(hass)

    devices = await client.get_devices()
    for device in devices:
        _LOGGER.info("Device type: %s", device.type)
        if device.valid:
            if device.type in MODELS_SUPPORTED:
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    connections={(dr.CONNECTION_NETWORK_MAC, device.mac)},
                    identifiers={(DOMAIN, device.guid)},
                    manufacturer=MANUFACTURER,
                    model=MODELS_SUPPORTED.get(device.type),
                    model_id=device.type,
                    name=device.name,
                    sw_version=device.firmware,
                    hw_version=device.hardware,
                )
            else:
                _LOGGER.info("Unsupported device type: %s", device.type)
        else:
            _LOGGER.info("Skipped device %s (not valid)", device.name)

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
