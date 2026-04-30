"""Platform for switch integration."""

import abc
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionError, TionZoneDevice
from .const import DOMAIN, Heater, TionDeviceType
from .coordinator import TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


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

        if device.type in [
            TionDeviceType.BREEZER_3S,
            TionDeviceType.BREEZER_4S,
        ]:
            entities.append(TionBacklightSwitch(coordinator, device))
            entities.append(TionBreezerSoundSwitch(coordinator, device))
            if device.data.heater_installed or device.data.heater_type is not None:
                entities.append(TionBreezerHeaterSwitch(coordinator, device))
        elif device.type in [
            TionDeviceType.MAGIC_AIR,
            TionDeviceType.MODULE_CO2,
        ]:
            entities.append(TionBacklightSwitch(coordinator, device))

    async_add_entities(entities)
    return True


class TionSwitch(CoordinatorEntity[TionDataUpdateCoordinator], SwitchEntity, abc.ABC):
    """Abstract Tion switch."""

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
    @abc.abstractmethod
    def name(self) -> str:
        """Return the name of the switch."""

    @property
    def is_on(self) -> bool | None:
        """Return True if entity is on."""
        return self._is_on

    async def async_turn_on(self) -> None:
        """Turn on Tion switch."""
        await self._load()
        self._is_on = True
        await self._send()

    async def async_turn_off(self) -> None:
        """Turn off Tion switch."""
        await self._load()
        self._is_on = False
        await self._send()

    async def _load(self, force=False) -> bool:
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
            await self.coordinator.client.send_settings(
                guid=self._device.guid, data=data
            )
        except TionError as err:
            raise HomeAssistantError(
                f"Unable to update {self.name} settings: {err}"
            ) from err

        await self.coordinator.async_request_refresh()

    async def _async_send_breezer(self, **kwargs) -> None:
        """Send breezer data and refresh coordinator data."""
        try:
            await self.coordinator.client.send_breezer(**kwargs)
        except TionError as err:
            raise HomeAssistantError(f"Unable to update {self.name}: {err}") from err

        await self.coordinator.async_request_refresh()

    @abc.abstractmethod
    async def _send(self) -> None:
        """Send new switch device data to API."""


class TionBacklightSwitch(TionSwitch):
    """Tion backlight switch."""

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
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device.name} Backlight"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:led-on" if self._is_on else "mdi:led-off"

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if await super()._load(force=force):
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated backlight state."""
        self._is_on = bool(self._device.data.backlight)
        _LOGGER.debug(
            "%s: fetched settings data: backlight=%s",
            self.name,
            self.is_on,
        )

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


class TionBreezerSoundSwitch(TionSwitch):
    """Tion MagicAir backlight switch."""

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
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device.name} Sound"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:music-note" if self._is_on else "mdi:music-note-off"

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if await super()._load(force=force):
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated sound state."""
        self._is_on = bool(self._device.data.sound_is_on)
        _LOGGER.debug(
            "%s: fetched settings data: sound=%s",
            self.name,
            self.is_on,
        )

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
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device.name} Heater"

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

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if await super()._load(force=force):
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated heater state."""
        self._is_on = self._heater_enabled
        _LOGGER.debug(
            "%s: fetched settings data: heater_mode=%s, heater_enabled=%s",
            self.name,
            self._device.data.heater_mode,
            self._device.data.heater_enabled,
        )

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            return

        try:
            breezer_t_set = int(self._device.data.t_set)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert breezer temperature set value to int: %s. Error: %s",
                self.name,
                self._device.data.t_set,
                e,
            )
            return

        try:
            breezer_speed = int(self._device.data.speed)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert breezer speed value to int: %s. Error: %s",
                self.name,
                self._device.data.speed,
                e,
            )
            return

        if self._device.type == TionDeviceType.BREEZER_4S:
            self._device.data.heater_mode = Heater.ON if self._is_on else Heater.OFF
        else:
            self._device.data.heater_enabled = self._is_on

        _LOGGER.debug(
            "%s: pushing new breezer data: is_on=%s, t_set=%s, speed=%s, speed_min_set=%s, speed_max_set=%s, heater_enabled=%s, heater_mode=%s, gate=%s",
            self.name,
            self._device.data.is_on,
            breezer_t_set,
            breezer_speed,
            self._device.data.speed_min_set,
            self._device.data.speed_max_set,
            self._device.data.heater_enabled,
            self._device.data.heater_mode,
            self._device.data.gate,
        )

        await self._async_send_breezer(
            guid=self._device.guid,
            is_on=self._device.data.is_on,
            t_set=breezer_t_set,
            speed=breezer_speed,
            speed_min_set=self._device.data.speed_min_set,
            speed_max_set=self._device.data.speed_max_set,
            heater_enabled=self._device.data.heater_enabled,
            heater_mode=self._device.data.heater_mode,
            gate=self._device.data.gate,
        )
