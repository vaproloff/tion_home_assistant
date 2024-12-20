"""Platform for binary sensor integration."""

import abc
import logging

from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .client import TionClient, TionZoneDevice
from .const import DOMAIN, TionDeviceType

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up sensor Tion entities."""
    client: TionClient = hass.data[DOMAIN][entry.entry_id]

    entities = [
        TionFilterNeedReplacementBinarySensor(hass, client, device)
        for device in await client.get_devices()
        if device.type in [TionDeviceType.BREEZER_3S, TionDeviceType.BREEZER_4S]
    ]

    async_add_entities(entities)
    return True


class TionBinarySensor(BinarySensorEntity, abc.ABC):
    """Abstract Tion binary sensor."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize binary sensor device."""
        self.hass = hass
        self._api = client
        self._device = device

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.guid)},
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device.is_online and self._device.valid

    @property
    @abc.abstractmethod
    def unique_id(self):
        """Return a unique id identifying the entity."""

    @property
    @abc.abstractmethod
    def name(self):
        """Return the name of the binary sensor."""

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await self._load()
        await super().async_added_to_hass()

    async def async_update(self):
        """Fetch new state data for the binary sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        await self._load()

    async def _load(self, force=False) -> bool:
        if device_data := await self._api.get_device(
            guid=self._device.guid, force=force
        ):
            self._device = device_data
            return True

        return False


class TionFilterNeedReplacementBinarySensor(TionBinarySensor):
    """Tion Breezer filter need replacement binary sensor."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(hass, client, device)

        self._attr_device_class = BinarySensorDeviceClass.PROBLEM

        _LOGGER.debug("hass: %s", self.hass)

        state = bool(self._device.data.filter_need_replace)
        if state:
            persistent_notification.async_create(
                self.hass,
                f"{self._device.name}' needs filters replacement.",
                title="Tion",
                notification_id="filter_need_replacement",
            )
        self._attr_is_on = state

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_filter_need_replacement"

    @property
    def name(self):
        """Return the name of the binary sensor."""
        return f"{self._device.name} Filter Need Replacement"

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            new_state = bool(self._device.data.filter_need_replace)

            if new_state and not self._attr_is_on:
                persistent_notification.async_create(
                    self.hass,
                    f"{self._device.name}' needs filters replacement.",
                    title="Tion",
                    notification_id="filter_need_replacement",
                )
            else:
                persistent_notification.async_dismiss(
                    self.hass,
                    notification_id="filter_need_replacement",
                )

            self._attr_is_on = new_state

            _LOGGER.debug(
                "%s: fetched data: filter_need_replace=%s",
                self.name,
                self._device.data.filter_need_replace,
            )

        return self.available
