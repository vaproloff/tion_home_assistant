"""Platform for binary sensor integration."""

import abc
import logging

from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionZoneDevice
from .const import DOMAIN, TionDeviceType
from .coordinator import TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up sensor Tion entities."""
    coordinator: TionDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        TionFilterNeedReplacementBinarySensor(hass, coordinator, device)
        for device in coordinator.get_devices()
        if device.guid
        and device.type in [TionDeviceType.BREEZER_3S, TionDeviceType.BREEZER_4S]
    ]

    async_add_entities(entities)
    return True


class TionBinarySensor(
    CoordinatorEntity[TionDataUpdateCoordinator], BinarySensorEntity, abc.ABC
):
    """Abstract Tion binary sensor."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize binary sensor device."""
        super().__init__(coordinator)
        self.hass = hass
        self._device = device

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.guid)},
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self._device is not None
            and self._device.is_online
            and self._device.valid
        )

    @property
    @abc.abstractmethod
    def unique_id(self):
        """Return a unique id identifying the entity."""

    @property
    @abc.abstractmethod
    def name(self):
        """Return the name of the binary sensor."""

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated device data."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if device_data := self.coordinator.get_device(self._device.guid):
            self._device = device_data
        self._handle_device_update()
        super()._handle_coordinator_update()

    async def _load(self, force=False) -> bool:
        if device_data := self.coordinator.get_device(self._device.guid):
            self._device = device_data
            return True

        return False


class TionFilterNeedReplacementBinarySensor(TionBinarySensor):
    """Tion Breezer filter need replacement binary sensor."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(hass, coordinator, device)

        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_is_on = bool(self._device.data.filter_need_replace)
        self._notification_id = f"tion_filter_need_replacement_{self._device.guid}"

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        self._sync_filter_notification(None, self._attr_is_on)

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_filter_need_replacement"

    @property
    def name(self):
        """Return the name of the binary sensor."""
        return f"{self._device.name} Filter Need Replacement"

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated filter replacement state."""
        old_state = self._attr_is_on
        new_state = bool(self._device.data.filter_need_replace)
        self._sync_filter_notification(old_state, new_state)
        self._attr_is_on = new_state

        _LOGGER.debug(
            "%s: fetched data: filter_need_replace=%s",
            self.name,
            self._device.data.filter_need_replace,
        )

    @callback
    def _sync_filter_notification(
        self, old_state: bool | None, new_state: bool | None
    ) -> None:
        """Create or dismiss the filter replacement notification."""
        if new_state and old_state is not True:
            persistent_notification.async_create(
                self.hass,
                f"{self._device.name} needs filters replacement.",
                title="Tion",
                notification_id=self._notification_id,
            )
        elif old_state is True and not new_state:
            persistent_notification.async_dismiss(
                self.hass,
                notification_id=self._notification_id,
            )
