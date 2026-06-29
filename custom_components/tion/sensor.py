"""Platform for sensor integration."""

import abc
import logging
from math import ceil

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
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

    entities = []
    devices = coordinator.get_devices()
    for device in devices:
        if not device.guid:
            continue

        if device.type in [
            TionDeviceType.BREEZER_O2,
            TionDeviceType.BREEZER_3S,
            TionDeviceType.BREEZER_4S,
        ]:
            entities.append(TionTemperatureInSensor(coordinator, device))
            entities.append(TionTemperatureOutSensor(coordinator, device))
            entities.append(TionFilterReplacementSensor(coordinator, device))
        elif device.type in [
            TionDeviceType.MAGIC_AIR,
            TionDeviceType.MODULE_CO2,
        ]:
            entities.append(TionTemperatureSensor(coordinator, device))
            entities.append(TionHumiditySensor(coordinator, device))
            entities.append(TionCO2Sensor(coordinator, device))

            if device.data.pm25 != "NaN":
                entities.append(TionPM25Sensor(coordinator, device))

    entities.append(TionApiProfileSensor(coordinator, entry.entry_id))

    async_add_entities(entities)
    return True


class TionApiProfileSensor(CoordinatorEntity[TionDataUpdateCoordinator], SensorEntity):
    """Diagnostic sensor exposing the active Tion cloud API profile.

    Account-level (no device) and disabled by default. It stays available while
    the coordinator is updating even when the breezers are offline, so the
    serving endpoint can be inspected during an outage.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "api_profile"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TionDataUpdateCoordinator, entry_id: str) -> None:
        """Initialize the API profile sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_api_profile"

    @property
    def native_value(self) -> str:
        """Return the name of the active API profile."""
        return self.coordinator.client.active_profile


class TionSensor(CoordinatorEntity[TionDataUpdateCoordinator], SensorEntity, abc.ABC):
    """Abstract Tion sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
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
    @abc.abstractmethod
    def unique_id(self):
        """Return a unique id identifying the entity."""

    @property
    @abc.abstractmethod
    def native_value(self):
        """Return the state of the sensor."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if device_data := self.coordinator.get_device(self._device.guid):
            self._device = device_data
        super()._handle_coordinator_update()


class TionTemperatureSensor(TionSensor):
    """Tion room temperature sensor."""

    _attr_translation_key = "temperature"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_temperature"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.data.temperature if self.available else None


class TionHumiditySensor(TionSensor):
    """Tion room humidity sensor."""

    _attr_translation_key = "humidity"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_humidity"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.data.humidity if self.available else None


class TionCO2Sensor(TionSensor):
    """Tion room CO2 sensor."""

    _attr_translation_key = "co2"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.CO2
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_co2"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.data.co2 if self.available else None


class TionPM25Sensor(TionSensor):
    """Tion room PM25 sensor."""

    _attr_translation_key = "pm25"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.PM25
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_pm25"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.data.pm25 if self.available else None


class TionTemperatureInSensor(TionSensor):
    """Tion inside air flow temperature sensor."""

    _attr_translation_key = "temperature_in"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_temperature_in"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.data.t_in if self.available else None


class TionTemperatureOutSensor(TionSensor):
    """Tion outside air flow temperature sensor."""

    _attr_translation_key = "temperature_out"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_temperature_out"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._device.data.t_out if self.available else None


class TionFilterReplacementSensor(TionSensor):
    """Tion Breezer filter replacement sensor."""

    _attr_translation_key = "filter_replacement_days"

    def __init__(
        self,
        coordinator: TionDataUpdateCoordinator,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(coordinator, device)

        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTime.DAYS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_filter_replacement_days"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return (
            max(0, ceil(self._device.data.filter_time_seconds / 86400))
            if self.available
            else None
        )
