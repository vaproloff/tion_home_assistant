"""Platform for number integration."""

import abc
import logging
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberExtraStoredData,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionZone, TionZoneDevice
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

    async def _load(self) -> bool:
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

    async def _push(self) -> None:
        """Apply desired state now (PID recompute + reconcile), then refresh.

        A min/max change is a local PID input, so going through ``apply_desired``
        lets the PID recompute the speed against the new limit in the same pass;
        a single command then carries both the limit and the new speed, instead
        of dispatching the old speed now and the recomputed one a cycle later.
        """
        if self.coordinator.data is not None:
            self.coordinator.apply_desired(self.coordinator.data)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


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
        """Write the zone target CO2 into the desired state."""
        zone = self.coordinator.get_device_zone(self._device.guid)
        if not self.available or zone is None:
            raise HomeAssistantError(f"{self.name} is unavailable")

        self._target_co2 = value
        self.coordinator.reconciler.set_zone(zone.guid, {"co2": int(value)})
        await self._push()

    async def _load(self) -> bool:
        await super()._load()
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


class TionLocalTargetCO2(TionNumber, RestoreNumber):
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

    @property
    def extra_restore_state_data(self) -> NumberExtraStoredData:
        """Persist the raw local target, even while the entity is unavailable.

        RestoreNumber would persist ``native_value``, which this entity reports
        as ``None`` while unavailable (e.g. the breezer's gateway is offline);
        persist the stored target directly so a reload never loses it.
        """
        return NumberExtraStoredData(
            self.native_max_value,
            self.native_min_value,
            self.native_step,
            self.native_unit_of_measurement,
            self._target_co2,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the local target CO2 across restarts and reloads."""
        await super().async_added_to_hass()
        if (
            last_number_data := await self.async_get_last_number_data()
        ) is not None and last_number_data.native_value is not None:
            self._target_co2 = last_number_data.native_value

        self.coordinator.pid_manager.set_target_co2(self._device.guid, self._target_co2)

    async def async_set_native_value(self, value: float) -> None:
        """Set new local target CO2 value."""
        self._target_co2 = value
        self.coordinator.pid_manager.set_target_co2(self._device.guid, value)
        self.async_write_ha_state()


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
        """Write the breezer lower auto-speed limit into the desired state."""
        if not self.available:
            raise HomeAssistantError(f"{self.name} is unavailable")

        self._breezer_min_speed = value
        self.coordinator.reconciler.set_breezer(
            self._device.guid, {"speed_min_set": int(value)}
        )
        await self._push()

    async def _load(self) -> bool:
        if await super()._load():
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
        """Write the breezer upper auto-speed limit into the desired state."""
        if not self.available:
            raise HomeAssistantError(f"{self.name} is unavailable")

        self._breezer_max_speed = value
        self.coordinator.reconciler.set_breezer(
            self._device.guid, {"speed_max_set": int(value)}
        )
        await self._push()

    async def _load(self) -> bool:
        if await super()._load():
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
