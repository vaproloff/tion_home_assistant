"""Platform for number integration."""

import abc
import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionError, TionZone, TionZoneDevice
from .const import DEFAULT_TARGET_CO2, DOMAIN, TionDeviceType
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
        if device.guid and device.valid:
            if device.type in [
                TionDeviceType.BREEZER_O2,
                TionDeviceType.BREEZER_3S,
                TionDeviceType.BREEZER_4S,
            ]:
                entities.append(TionMinSpeed(coordinator, device))
                entities.append(TionMaxSpeed(coordinator, device))
                if coordinator.pid_manager.is_configured(device.guid):
                    entities.append(TionLocalTargetCO2(coordinator, device))
            elif device.type in [
                TionDeviceType.MAGIC_AIR,
                TionDeviceType.MODULE_CO2,
            ]:
                entities.append(TionTargetCO2(coordinator, device))

        else:
            _LOGGER.debug("Skipped device %s (not valid)", device.name)

    async_add_entities(entities)
    return True


class TionNumber(CoordinatorEntity[TionDataUpdateCoordinator], NumberEntity, abc.ABC):
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

        self._attr_mode = NumberMode.SLIDER

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

    @abc.abstractmethod
    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
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

    def _int_or_raise(self, value: Any, description: str) -> int:
        """Convert an API value to int or raise a service error."""
        try:
            return int(value)
        except (TypeError, ValueError) as err:
            raise HomeAssistantError(
                f"Unable to convert {description} value for {self.name}: {value}"
            ) from err

    async def _async_send_zone(self, guid: str, mode, co2: int) -> None:
        """Send zone data and refresh coordinator data."""
        try:
            await self.coordinator.async_send_zone(guid=guid, mode=mode, co2=co2)
        except TionError as err:
            raise HomeAssistantError(f"Unable to update {self.name}: {err}") from err

    async def _async_send_breezer(self, **kwargs) -> None:
        """Send breezer data and refresh coordinator data."""
        try:
            await self.coordinator.async_send_breezer(**kwargs)
        except TionError as err:
            raise HomeAssistantError(f"Unable to update {self.name}: {err}") from err

    @abc.abstractmethod
    async def _send(self) -> None:
        """Push new data to API."""


class TionTargetCO2(TionNumber):
    """Tion Target CO2 Level Number."""

    _attr_icon = "mdi:molecule-co2"
    _attr_translation_key = "target_co2"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._zone: TionZone | None = self.coordinator.get_device_zone(
            self._device.guid
        )

        self._attr_device_class = NumberDeviceClass.CO2
        self._attr_native_min_value = 550
        self._attr_native_max_value = 1500
        self._attr_native_step = 10

        self._target_co2: float | None = None
        self._handle_device_update()

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_target_co2"

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the number."""
        return (
            self._target_co2
            if self._zone is not None
            and self._zone.valid
            and self._target_co2 is not None
            else None
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        lock_key = self.coordinator.zone_mode_command_key_for_device(self._device.guid)
        async with self.coordinator.async_zone_mode_command(lock_key):
            await self._load()
            self._target_co2 = value
            await self._send()

    async def _load(self, force=False) -> bool:
        await super()._load(force=force)
        self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated target CO2 state."""
        self._zone = self.coordinator.get_device_zone(self._device.guid)
        if self._zone is None:
            self._target_co2 = None
            return

        try:
            self._target_co2 = float(self._zone.mode.auto_set.co2)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert target CO2 value to float: %s. Error: %s",
                self.name,
                self._zone.mode.auto_set.co2,
                e,
            )
            self._target_co2 = None

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available or self._zone is None:
            raise HomeAssistantError(f"{self.name} is unavailable")

        target_co2 = self._int_or_raise(self._target_co2, "target CO2")

        _LOGGER.debug(
            "%s: pushing new zone data: mode=%s, target_co2=%s",
            self.name,
            self._zone.mode.current,
            target_co2,
        )

        await self._async_send_zone(
            guid=self._zone.guid, mode=self._zone.mode.current, co2=target_co2
        )


class TionLocalTargetCO2(TionNumber, RestoreEntity):
    """Local target CO2 level for an external CO2 PID controller."""

    _attr_icon = "mdi:molecule-co2"
    _attr_translation_key = "external_target_co2"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize local target CO2 number."""
        super().__init__(coordinator, device)

        self._attr_device_class = NumberDeviceClass.CO2
        self._attr_native_min_value = 550
        self._attr_native_max_value = 1500
        self._attr_native_step = 10

        self._target_co2: float = DEFAULT_TARGET_CO2
        self._target_co2 = self.coordinator.pid_manager.get_target_co2(
            self._device.guid
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(
            super().available
            and self.coordinator.pid_manager.is_configured(self._device.guid)
        )

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_external_target_co2"

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the number."""
        return self._target_co2 if self.available else None

    async def async_added_to_hass(self) -> None:
        """Restore local target CO2."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._target_co2 = float(last_state.state)
            except TypeError, ValueError:
                self._target_co2 = DEFAULT_TARGET_CO2

        self.coordinator.pid_manager.set_target_co2(self._device.guid, self._target_co2)

    async def async_set_native_value(self, value: float) -> None:
        """Set new local target CO2 value."""
        self._target_co2 = value
        self.coordinator.pid_manager.set_target_co2(self._device.guid, value)
        self.async_write_ha_state()

    async def _send(self) -> None:
        """No cloud command is needed for local target CO2."""


class TionMinSpeed(TionNumber):
    """Tion Minimum Speed Number for Breezer Auto Mode."""

    _attr_icon = "mdi:fan-chevron-down"
    _attr_translation_key = "min_speed_set"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._attr_native_min_value = 0
        self._attr_native_max_value = device.max_speed
        self._attr_native_step = 1

        self._breezer_min_speed: float | None = None
        self._handle_device_update()

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_min_speed_set"

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the number."""
        return (
            self._breezer_min_speed
            if self._device.valid and self._breezer_min_speed is not None
            else None
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        async with self.coordinator.async_breezer_mode_command(self._device.guid):
            await self._load()
            self._breezer_min_speed = value
            await self._send()

    async def _load(self, force=False) -> bool:
        if await super()._load(force=force):
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated min speed state."""
        try:
            self._breezer_min_speed = float(self._device.data.speed_min_set)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert breezer min speed set value to float: %s. Error: %s",
                self.name,
                self._device.data.speed_min_set,
                e,
            )
            self._breezer_min_speed = None

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            raise HomeAssistantError(f"{self.name} is unavailable")

        breezer_min_speed = self._int_or_raise(self._breezer_min_speed, "min speed")
        breezer_t_set = self._int_or_raise(
            self._device.data.t_set, "target temperature"
        )
        breezer_speed = self._int_or_raise(self._device.data.speed, "speed")

        _LOGGER.debug(
            "%s: pushing new breezer data: is_on=%s, t_set=%s, speed=%s, speed_min_set=%s, speed_max_set=%s, heater_enabled=%s, heater_mode=%s, gate=%s",
            self.name,
            self._device.data.is_on,
            breezer_t_set,
            breezer_speed,
            breezer_min_speed,
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
            speed_min_set=breezer_min_speed,
            speed_max_set=self._device.data.speed_max_set,
            heater_enabled=self._device.data.heater_enabled,
            heater_mode=self._device.data.heater_mode,
            gate=self._device.data.gate,
        )


class TionMaxSpeed(TionNumber):
    """Tion Maximum Speed Number for Breezer Auto Mode."""

    _attr_icon = "mdi:fan-chevron-up"
    _attr_translation_key = "max_speed_set"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(coordinator, device)

        self._attr_native_min_value = 0
        self._attr_native_max_value = device.max_speed
        self._attr_native_step = 1

        self._breezer_max_speed: float | None = None
        self._handle_device_update()

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_max_speed_set"

    @property
    def native_value(self) -> int | None:
        """Return the value reported by the number."""
        return (
            self._breezer_max_speed
            if self._device.valid and self._breezer_max_speed is not None
            else None
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        async with self.coordinator.async_breezer_mode_command(self._device.guid):
            await self._load()
            self._breezer_max_speed = value
            await self._send()

    async def _load(self, force=False) -> bool:
        if await super()._load(force=force):
            self._handle_device_update()

        return self.available

    @callback
    def _handle_device_update(self) -> None:
        """Handle updated max speed state."""
        try:
            self._breezer_max_speed = float(self._device.data.speed_max_set)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert breezer max speed set value to float: %s. Error: %s",
                self.name,
                self._device.data.speed_max_set,
                e,
            )
            self._breezer_max_speed = None

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            raise HomeAssistantError(f"{self.name} is unavailable")

        breezer_max_speed = self._int_or_raise(self._breezer_max_speed, "max speed")
        breezer_t_set = self._int_or_raise(
            self._device.data.t_set, "target temperature"
        )
        breezer_speed = self._int_or_raise(self._device.data.speed, "speed")

        _LOGGER.debug(
            "%s: pushing new breezer data: is_on=%s, t_set=%s, speed=%s, speed_min_set=%s, speed_max_set=%s, heater_enabled=%s, heater_mode=%s, gate=%s",
            self.name,
            self._device.data.is_on,
            breezer_t_set,
            breezer_speed,
            self._device.data.speed_min_set,
            breezer_max_speed,
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
            speed_max_set=breezer_max_speed,
            heater_enabled=self._device.data.heater_enabled,
            heater_mode=self._device.data.heater_mode,
            gate=self._device.data.gate,
        )
