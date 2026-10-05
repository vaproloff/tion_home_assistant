"""The Tion integration."""

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api import (
    TionAccount,
    TionAuthError,
    TionConnectionError,
    TionDeviceKey,
    TionError,
    TionTokens,
    view,
)
from .const import CONF_DEVICE_KEY, DOMAIN, MANUFACTURER, MODEL_NAMES, PLATFORMS
from .coordinator import TionConfigEntry, TionCoordinator
from .session import async_create_cloud

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: TionConfigEntry) -> bool:
    """Set up a Tion account from a config entry."""
    try:
        device_key = TionDeviceKey.from_pem(entry.data[CONF_DEVICE_KEY])
        tokens = TionTokens.from_dict(entry.data)
    except (KeyError, TypeError, ValueError) as err:
        # Entries from before the v4 login hold a username and a password.
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err

    auth, cloud = await async_create_cloud(
        hass,
        device_key,
        tokens,
        lambda coro, name: entry.async_create_background_task(hass, coro, name),
    )

    @callback
    def _save_tokens(new_tokens: TionTokens) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **new_tokens.as_dict()}
        )

    entry.async_on_unload(auth.add_update_listener(_save_tokens))

    try:
        await cloud.async_start()
    except TionAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except TionConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cloud_unavailable"
        ) from err
    except TionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cloud_error",
            translation_placeholders={"message": str(err)},
        ) from err
    # Unload callbacks run last-in first-out: the coordinator, registered
    # below, stops listening before the cloud stops.
    entry.async_on_unload(cloud.async_stop)

    entry.runtime_data = TionCoordinator(hass, entry, cloud)
    _register_devices(hass, entry, cloud.account)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TionConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TionConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device the account no longer has."""
    account = entry.runtime_data.data
    return not any(
        domain == DOMAIN and account.device(device_id) is not None
        for domain, device_id in device_entry.identifiers
    )


def _register_devices(
    hass: HomeAssistant, entry: TionConfigEntry, account: TionAccount
) -> None:
    registry = dr.async_get(hass)
    for device in account.devices():
        if view(device) is None:
            _LOGGER.debug("Skipping unsupported Tion device model %s", device.model)
            continue
        room = account.room_of(device)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.id)},
            connections={(dr.CONNECTION_NETWORK_MAC, mac) for mac in device.macs},
            manufacturer=MANUFACTURER,
            model=MODEL_NAMES[device.product_id],
            model_id=device.product_id,
            name=device.name,
            sw_version=str(device.firmware),
            hw_version=str(device.hardware),
            suggested_area=room.name if room is not None else None,
        )
