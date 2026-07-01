"""Platform for switch integration."""

import abc
from contextlib import AbstractAsyncContextManager, nullcontext
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionError, TionZone, TionZoneDevice
from .const import BREEZER_TYPES, DOMAIN, Heater, TionDeviceType, ZoneMode
from .coordinator import TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _zone_has_local_pid(
    coordinator: TionDataUpdateCoordinator, device_guid: str
) -> bool:
    """Return if a device zone has at least one breezer with local PID configured."""
    zone = coordinator.get_device_zone(device_guid)
    if zone is None:
        return False

    return any(
        device.guid
        and device.type in BREEZER_TYPES
        and coordinator.pid_manager.is_configured(device.guid)
        for device in zone.devices
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up switch Tion entities."""
    coordinator: TionDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    devices = coordinator.get_devices()
    for device in devices:
        if not device.guid:
            continue

        if device.type in BREEZER_TYPES:
            if device.type in (TionDeviceType.BREEZER_3S, TionDeviceType.BREEZER_4S):
                entities.append(TionBacklightSwitch(coordinator, device))
                entities.append(TionBreezerSoundSwitch(coordinator, device))
            if device.data.heater_installed or device.data.heater_type is not None:
                entities.append(TionBreezerHeaterSwitch(coordinator, device))
        elif device.type in [
            TionDeviceType.MAGIC_AIR,
            TionDeviceType.MODULE_CO2,
        ]:
            entities.append(TionBacklightSwitch(coordinator, device))
            if device.type == TionDeviceType.MAGIC_AIR:
                if _zone_has_local_pid(coordinator, device.guid):
                    _LOGGER.debug(
                        "%s: skipped auto mode switch because local PID is configured in the same zone",
                        device.name,
                    )
                else:
                    entities.append(TionAutoModeSwitch(coordinator, device))

    async_add_entities(entities)
    return True


class TionSwitch(CoordinatorEntity[TionDataUpdateCoordinator], SwitchEntity, abc.ABC):
    """Abstract Tion switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator)
        self._device = device

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.guid)},
        )

        self._is_on: bool | None = None

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
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""

    @property
    def is_on(self) -> bool | None:
        """Return True if entity is on."""
        return self._is_on

    async def async_turn_on(self) -> None:
        """Turn on Tion switch."""
        async with self._async_command_lock():
            await self._load()
            self._is_on = True
            await self._send()

    async def async_turn_off(self) -> None:
        """Turn off Tion switch."""
        async with self._async_command_lock():
            await self._load()
            self._is_on = False
            await self._send()

    def _async_command_lock(self) -> AbstractAsyncContextManager[None]:
        """Return the command critical section for this switch."""
        return nullcontext()

    async def _load(self) -> bool:
        if device_data := self.coordinator.get_device(self._device.guid):
            self._device = device_data
            return True

        return False

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

    async def _async_send_settings(self, data: dict[str, Any]) -> None:
        """Send settings and refresh coordinator data."""
        try:
            await self.coordinator.async_send_settings(
                guid=self._device.guid, data=data
            )
        except TionError as err:
            raise HomeAssistantError(
                f"Unable to update {self.name} settings: {err}"
            ) from err

    async def _push(self) -> None:
        """Reconcile desired state now (optimistic + dispatch), then refresh."""
        if self.coordinator.data is not None:
            self.coordinator.reconciler.reconcile(self.coordinator.data)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @abc.abstractmethod
    async def _send(self) -> None:
        """Send new switch device data to API."""


class TionBacklightSwitch(TionSwitch):
    """Tion backlight switch."""

    _attr_translation_key = "backlight"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._is_on = bool(device.data.backlight)

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_backlight"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:led-on" if self._is_on else "mdi:led-off"

    def _async_command_lock(self) -> AbstractAsyncContextManager[None]:
        """Serialize backlight writes on the settings endpoint."""
        return self.coordinator.async_settings_command(self._device.guid)

    async def _load(self) -> bool:
        """Update device data from API."""
        if await super()._load():
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated backlight state."""
        self._is_on = bool(self._device.data.backlight)

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            return

        data = {"backlight": 1 if self._is_on else 0}

        _LOGGER.debug(
            "%s: pushing new settings data: backlight=%s",
            self.name,
            self._is_on,
        )
        await self._async_send_settings(data)


class TionAutoModeSwitch(TionSwitch):
    """Tion MagicAir auto mode switch."""

    _attr_translation_key = "auto_mode"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._is_on = self._auto_enabled

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_auto_mode"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:fan-auto" if self._is_on else "mdi:fan"

    @property
    def _auto_enabled(self) -> bool | None:
        """Return if Auto mode enabled now."""
        zone: TionZone | None = self.coordinator.get_device_zone(self._device.guid)
        if zone is not None:
            return zone.mode.current == ZoneMode.AUTO

        return None

    async def _load(self) -> bool:
        """Update device and zone data from API."""
        await super()._load()
        self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated auto mode state."""
        self._is_on = self._auto_enabled

        if self._is_on is None:
            _LOGGER.debug("%s: zone is unavailable", self.name)

    async def _send(self) -> None:
        """Write the zone auto/manual mode into the desired state."""
        zone: TionZone | None = self.coordinator.get_device_zone(self._device.guid)

        if not self.available or zone is None:
            raise HomeAssistantError(f"{self.name} zone is unavailable")

        mode = ZoneMode.AUTO if self._is_on else ZoneMode.MANUAL
        _LOGGER.debug("%s: writing desired zone mode=%s", self.name, mode)
        self.coordinator.reconciler.set_zone(zone.guid, {"mode": mode})
        await self._push()


class TionBreezerSoundSwitch(TionSwitch):
    """Tion MagicAir backlight switch."""

    _attr_translation_key = "sound"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._is_on = bool(device.data.sound_is_on)

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_sound"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:music-note" if self._is_on else "mdi:music-note-off"

    def _async_command_lock(self) -> AbstractAsyncContextManager[None]:
        """Serialize sound writes on the settings endpoint."""
        return self.coordinator.async_settings_command(self._device.guid)

    async def _load(self) -> bool:
        """Update device data from API."""
        if await super()._load():
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated sound state."""
        self._is_on = bool(self._device.data.sound_is_on)

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            return

        data = {"sound": 1 if self._is_on else 0}

        _LOGGER.debug(
            "%s: pushing new settings data: sound=%s",
            self.name,
            self._is_on,
        )
        await self._async_send_settings(data)


class TionBreezerHeaterSwitch(TionSwitch):
    """Tion Breezer Heater switch."""

    _attr_translation_key = "heater"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._is_on = self._heater_enabled

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_heater"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:radiator" if self._is_on else "mdi:radiator-disabled"

    @property
    def _heater_enabled(self) -> bool:
        """Return if heater active now."""
        if self._device.type == TionDeviceType.BREEZER_4S:
            return self._device.data.heater_mode == Heater.ON

        return bool(self._device.data.heater_enabled)

    async def _load(self) -> bool:
        """Update device data from API."""
        if await super()._load():
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated heater state."""
        self._is_on = self._heater_enabled

    async def _send(self) -> None:
        """Write the breezer heater state into the desired state."""
        if not self.available:
            return

        if self._device.type == TionDeviceType.BREEZER_4S:
            fields = {"heater_mode": Heater.ON if self._is_on else Heater.OFF}
        else:
            fields = {"heater_enabled": self._is_on}

        _LOGGER.debug("%s: writing desired heater fields %s", self.name, fields)
        self.coordinator.reconciler.set_breezer(self._device.guid, fields)
        await self._push()
