"""Platform for switch integration."""

import abc
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .client import TionClient, TionZoneDevice
from .const import DOMAIN, TionDeviceType

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up switch Tion entities."""
    client: TionClient = hass.data[DOMAIN][entry.entry_id]

    entities = []
    devices = await client.get_devices()
    for device in devices:
        if device.valid:
            if device.type in [
                TionDeviceType.BREEZER_3S,
                TionDeviceType.BREEZER_4S,
            ]:
                entities.append(TionBacklightSwitch(client, device))
                entities.append(TionBreezerSoundSwitch(client, device))
            elif device.type == TionDeviceType.MAGIC_AIR:
                entities.append(TionBacklightSwitch(client, device))

        else:
            _LOGGER.info("Skipped device %s (not valid)", device.name)

    async_add_entities(entities)
    return True


class TionSwitch(SwitchEntity, abc.ABC):
    """Abstract Tion switch."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        self._api = client
        self._device_data = device

        self._device_guid = self._device_data.guid
        self._device_name = self._device_data.name
        self._device_valid = self._device_data.valid

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_guid)},
        )

        self._is_on: bool | None = None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device_valid

    @property
    @abc.abstractmethod
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the name of the switch."""

    @property
    def is_on(self) -> bool | None:
        """Return True if entity is on."""
        return self._is_on

    async def async_update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        await self._load()

    async def async_turn_on(self) -> None:
        """Turn on Tion switch."""
        self._is_on = True
        await self._send()

    async def async_turn_off(self) -> None:
        """Turn off Tion switch."""
        self._is_on = False
        await self._send()

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if await self.__load(force=force):
            self._device_name = self._device_data.name
            self._device_guid = self._device_data.guid
            self._device_valid = self._device_data.valid
            return True

        return False

    async def __load(self, force=False) -> bool:
        if device_data := await self._api.get_device(
            guid=self._device_guid, force=force
        ):
            self._device_data = device_data
            return True

        return False

    @abc.abstractmethod
    async def _send(self) -> None:
        """Send new switch device data to API."""


class TionBacklightSwitch(TionSwitch):
    """Tion backlight switch."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(client, device)

        self._is_on = bool(device.data.backlight)

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_backlight"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device_name} Backlight"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:led-on" if self._is_on else "mdi:led-off"

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if await super()._load(force=force):
            self._is_on = bool(self._device_data.data.backlight)

            _LOGGER.debug(
                "%s: fetched settings data: backlight=%s",
                self.name,
                self.is_on,
            )

        return self.available

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self._device_valid:
            return

        data = {"backlight": 1 if self._is_on else 0}

        _LOGGER.debug(
            "%s: pushing new settings data: backlight=%s",
            self.name,
            self._is_on,
        )
        await self._api.send_settings(guid=self._device_guid, data=data)


class TionBreezerSoundSwitch(TionSwitch):
    """Tion MagicAir backlight switch."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(client, device)

        self._is_on = bool(device.data.sound_is_on)

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_sound"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device_name} Sound"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:music-note" if self._is_on else "mdi:music-note-off"

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if await super()._load(force=force):
            self._is_on = bool(self._device_data.data.sound_is_on)

            _LOGGER.debug(
                "%s: fetched settings data: sound=%s",
                self.name,
                self.is_on,
            )

        return self.available

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self._device_valid:
            return

        data = {"sound": 1 if self._is_on else 0}

        _LOGGER.debug(
            "%s: pushing new settings data: sound=%s",
            self.name,
            self._is_on,
        )
        await self._api.send_settings(guid=self._device_guid, data=data)
