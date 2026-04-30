"""The Tion component."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import TionClient
from .const import (
    AUTH_DATA,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MODELS_SUPPORTED,
    PLATFORMS,
)
from .coordinator import TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    _LOGGER.debug("Setting up %s config entry %s", DOMAIN, entry.entry_id)

    hass.data.setdefault(DOMAIN, {})

    async def update_auth_data(auth: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, AUTH_DATA: auth}
        )

    session = async_create_clientsession(hass)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    client = TionClient(
        session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        min_update_interval_sec=scan_interval,
        auth=entry.data.get(AUTH_DATA),
    )
    client.add_update_listener(update_auth_data)

    coordinator = TionDataUpdateCoordinator(hass, entry, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    device_registry = dr.async_get(hass)

    for device in coordinator.get_devices():
        _LOGGER.debug(
            "Adding device: type - %s, device name - %s", device.type, device.name
        )
        if not device.guid:
            _LOGGER.debug("Skipped device %s without guid", device.name)
            continue

        if device.type in MODELS_SUPPORTED:
            connections = (
                {(dr.CONNECTION_NETWORK_MAC, device.mac)} if device.mac else set()
            )
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                connections=connections,
                identifiers={(DOMAIN, device.guid)},
                manufacturer=MANUFACTURER,
                model=MODELS_SUPPORTED.get(device.type),
                model_id=device.type,
                name=device.name,
                sw_version=device.firmware,
                hw_version=device.hardware,
            )
        else:
            _LOGGER.debug("Unsupported device type: %s", device.type)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    """Handle updating entry options."""
    _LOGGER.debug("Updating %s config entry options %s", DOMAIN, entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    _LOGGER.debug("Unloading %s config entry %s", DOMAIN, entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading %s config entry %s", DOMAIN, entry.entry_id)
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
