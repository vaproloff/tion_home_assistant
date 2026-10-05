"""Filter reset button of Tion breezers."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Breezer
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, device_views


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a filter reset button to each breezer that can reset its filter."""
    coordinator = entry.runtime_data
    async_add_entities(
        TionResetFilterButton(coordinator, device_view)
        for device_view in device_views(coordinator)
        if isinstance(device_view, Breezer) and device_view.can_set("filter_reset")
    )


class TionResetFilterButton(TionEntity[Breezer], ButtonEntity):
    """Starts a new filter life after the filter is replaced."""

    _attr_translation_key = "reset_filters"

    def __init__(self, coordinator: TionCoordinator, breezer: Breezer) -> None:
        """Create the button for one breezer."""
        super().__init__(coordinator, breezer, "reset_filters")

    async def async_press(self) -> None:
        """Reset the filter counter."""
        await self.async_send_command(filter_reset=True)
