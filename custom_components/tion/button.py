"""Platform for button integration."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionError, TionZoneDevice
from .const import DOMAIN, TionDeviceType
from .coordinator import TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up button Tion entities."""
    coordinator: TionDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        TionResetFiltersButton(coordinator, device)
        for device in coordinator.get_devices()
        if device.guid
        and device.type
        in (
            TionDeviceType.BREEZER_O2,
            TionDeviceType.BREEZER_3S,
            TionDeviceType.BREEZER_4S,
        )
    ]

    async_add_entities(entities)
    return True


class TionResetFiltersButton(
    CoordinatorEntity[TionDataUpdateCoordinator], ButtonEntity
):
    """Tion Breezer reset filters button."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:air-filter"
    _attr_translation_key = "reset_filters"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize button device."""
        super().__init__(coordinator)
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
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_reset_filters"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if device_data := self.coordinator.get_device(self._device.guid):
            self._device = device_data
        super()._handle_coordinator_update()

    async def async_press(self) -> None:
        """Reset breezer filter replacement."""
        _LOGGER.debug("%s: resetting filter replacement timer", self._device.name)
        try:
            await self.coordinator.client.send_settings(
                self._device.guid, data={"reset_filter_timer": True}
            )
        except TionError as err:
            raise HomeAssistantError(
                f"Unable to reset filters for {self._device.name}: {err}"
            ) from err

        await self.coordinator.async_request_refresh()
