"""Filter replacement sensor of Tion breezers."""

from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Breezer
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, device_views


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a filter sensor to each breezer that reports its filter."""
    coordinator = entry.runtime_data
    async_add_entities(
        TionFilterBinarySensor(coordinator, device_view)
        for device_view in device_views(coordinator)
        if isinstance(device_view, Breezer)
        and device_view.supports("filter_needs_replacement")
    )


class TionFilterBinarySensor(TionEntity[Breezer], BinarySensorEntity):
    """On when the breezer asks for a new filter; also notifies the user."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "filter_need_replacement"

    def __init__(self, coordinator: TionCoordinator, breezer: Breezer) -> None:
        """Create the sensor for one breezer."""
        super().__init__(coordinator, breezer, "filter_need_replacement")
        self._notification_id = f"tion_filter_need_replacement_{breezer.id}"
        self._notified = False

    @property
    def is_on(self) -> bool | None:
        """Return True if the filter must be replaced."""
        return self.device_view.filter_needs_replacement

    async def async_added_to_hass(self) -> None:
        """Notify at once if the filter is already due."""
        await super().async_added_to_hass()
        self._update_notification()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_notification()
        super()._handle_coordinator_update()

    @callback
    def _update_notification(self) -> None:
        needs_replacement = self.is_on
        if needs_replacement and not self._notified:
            persistent_notification.async_create(
                self.hass,
                f"{self.device_view.device.name} needs filters replacement.",
                title="Tion",
                notification_id=self._notification_id,
            )
            self._notified = True
        elif needs_replacement is False and self._notified:
            persistent_notification.async_dismiss(self.hass, self._notification_id)
            self._notified = False
