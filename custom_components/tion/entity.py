"""Base entity of the Tion integration."""

from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    AutoControl,
    Breezer,
    Room,
    Station,
    TionApiError,
    TionAuthError,
    TionCommandError,
    TionConnectionError,
    TionDeviceTimeoutError,
    view,
)
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
        return self._latest_view() is not None and self.coordinator.device_available(
            self._view.id
        )

    @property
    def room(self) -> Room | None:
        """Return the device's room."""
        return self.coordinator.data.room_of(self.device_view.device)

    @property
    def room_auto(self) -> AutoControl | None:
        """Return the auto mode of the device's room, if it is set up."""
        return room.configured_auto if (room := self.room) is not None else None

    async def async_send_command(self, **changes: Any) -> None:
        """Set properties of the device; returns once the device confirms."""
        await self._async_cloud_call(
            lambda: self.coordinator.cloud.async_command(
                self.device_view.command(**changes)
            )
        )

    async def async_set_room_auto(self, **changes: Any) -> None:
        """Change the auto mode of the device's room."""

        def call() -> Awaitable[None]:
            if (room := self.room) is None:
                raise ValueError(f"{self.device_view.device.name} has no room")
            return self.coordinator.cloud.async_set_auto_control(room.id, **changes)

        await self._async_cloud_call(call)

    async def _async_cloud_call(self, call: Callable[[], Awaitable[None]]) -> None:
        try:
            await call()
        except TionCommandError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={
                    "device": self.device_view.device.name,
                    "message": err.message,
                },
            ) from err
        except TionDeviceTimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_not_responding",
                translation_placeholders={"device": self.device_view.device.name},
            ) from err
        except TionConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cloud_unavailable"
            ) from err
        except TionAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TionApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cloud_error",
                translation_placeholders={"message": str(err)},
            ) from err
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={"message": str(err)},
            ) from err

    def _latest_view(self) -> ViewT | None:
        device = self.coordinator.data.device(self._view.id)
        if device is None:
            return None
        if device is self._view.device:
            return self._view
        latest = view(device)
        return latest if isinstance(latest, type(self._view)) else None


class TionRoomAutoEntity[ViewT: Breezer | Station](TionEntity[ViewT]):
    """An entity of the auto mode of the device's room."""

    @property
    def available(self) -> bool:
        """Return True while the device is reachable and its room has auto mode."""
        return super().available and self.room_auto is not None
