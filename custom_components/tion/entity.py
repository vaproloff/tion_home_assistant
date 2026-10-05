"""Base entity of the Tion integration."""

from collections.abc import Iterator

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Breezer, Station, view
from .const import DOMAIN
from .coordinator import TionCoordinator


def device_views(coordinator: TionCoordinator) -> Iterator[Breezer | Station]:
    """Iterate over the views of the account's supported devices."""
    for device in coordinator.data.devices():
        if (device_view := view(device)) is not None:
            yield device_view


class TionEntity[ViewT: Breezer | Station](CoordinatorEntity[TionCoordinator]):
    """An entity of one device of the account."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TionCoordinator, device_view: ViewT, key: str | None
    ) -> None:
        """Bind the entity to a device; key None makes the device's main entity."""
        super().__init__(coordinator)
        self._view: ViewT = device_view
        self._attr_unique_id = (
            f"{device_view.id}_{key}" if key is not None else device_view.id
        )
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_view.id)})

    @property
    def device_view(self) -> ViewT:
        """Return the device's view, the last known one if it left the account."""
        if (latest := self._latest_view()) is not None:
            self._view = latest
        return self._view

    @property
    def available(self) -> bool:
        """Return True while the device and its gateway are reachable."""
        # Not CoordinatorEntity.available: while the live channel is up, a
        # failed periodic refresh does not make the pushed state stale.
        latest = self._latest_view()
        if not self.coordinator.channel_up or latest is None:
            return False
        device = latest.device
        gateway = (
            self.coordinator.data.device(device.parent_id)
            if device.parent_id is not None
            else None
        )
        return device.is_online and (gateway is None or gateway.is_online)

    def _latest_view(self) -> ViewT | None:
        device = self.coordinator.data.device(self._view.id)
        if device is None:
            return None
        if device is self._view.device:
            return self._view
        latest = view(device)
        return latest if isinstance(latest, type(self._view)) else None
