"""Platform for sensor integration."""

import abc
import logging

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
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .client import TionClient, TionZoneDevice
from .const import DOMAIN, TionDeviceType

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up climate Tion entities."""
    client: TionClient = hass.data[DOMAIN][entry.entry_id]

    entities = []
    devices = await client.get_devices()
    for device in devices:
        if device.valid:
            if device.type in [
                TionDeviceType.BREEZER_3S,
                TionDeviceType.BREEZER_4S,
            ]:
                entities.append(TionTemperatureInSensor(client, device))
                entities.append(TionTemperatureOutSensor(client, device))
            elif device.type in [
                TionDeviceType.MAGIC_AIR,
                TionDeviceType.MODULE_CO2,
            ]:
                entities.append(TionTemperatureSensor(client, device))
                entities.append(TionHumiditySensor(client, device))
                entities.append(TionCO2Sensor(client, device))

                if device.data.pm25 != "NaN":
                    entities.append(TionPM25Sensor(client, device))

        else:
            _LOGGER.info("Skipped device %s (not valid)", device.name)

    async_add_entities(entities)
    return True


class TionSensor(SensorEntity, abc.ABC):
    """Abstract Tion sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        self._api = client
        self._device_data = device

        self._device_guid = self._device_data.guid
        self._device_name = self._device_data.name
        self._device_valid = self._device_data.valid

        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_guid)},
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device_valid

    @property
    @abc.abstractmethod
    def unique_id(self):
        """Return a unique id identifying the entity."""

    @property
    @abc.abstractmethod
    def name(self):
        """Return the name of the sensor."""

    @property
    @abc.abstractmethod
    def state(self):
        """Return the state of the sensor."""

    async def async_update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        await self._load()

    async def _load(self, force=False):
        """Update device data from API."""
        if await self.__load(force=force):
            self._device_name = self._device_data.name
            self._device_guid = self._device_data.guid
            self._device_valid = self._device_data.valid
            return True

        return False

    async def __load(self, force=False) -> bool:
        if device_data := await self._api.get_device(
            guid=self._device_guid, force=force
        ):
            self._device_data = device_data
            return True

        return False


class TionTemperatureSensor(TionSensor):
    """Tion room temperature sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(client, device)

        self._temperature = device.data.temperature

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_temperature"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_name} Temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._temperature if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            self._temperature = self._device_data.data.temperature

        return self.available


class TionHumiditySensor(TionSensor):
    """Tion room humidity sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(client, device)

        self._humidity = device.data.humidity

        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_humidity"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_name} Humidity"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._humidity if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            self._humidity = self._device_data.data.humidity

        return self.available


class TionCO2Sensor(TionSensor):
    """Tion room CO2 sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(client, device)

        self._co2 = device.data.co2

        self._attr_device_class = SensorDeviceClass.CO2
        self._attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_co2"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_name} CO2"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._co2 if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            self._co2 = self._device_data.data.co2

        return self.available


class TionPM25Sensor(TionSensor):
    """Tion room PM25 sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(client, device)

        self._pm25 = device.data.pm25

        self._attr_device_class = SensorDeviceClass.PM25
        self._attr_native_unit_of_measurement = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_pm25"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_name} PM25"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._pm25 if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            self._pm25 = self._device_data.data.pm25

        return self.available


class TionTemperatureInSensor(TionSensor):
    """Tion inside air flow temperature sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(client, device)

        self._temperature = device.data.t_in

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_temperature_in"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_name} Inside Air Flow Temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._temperature if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            self._temperature = self._device_data.data.t_in

        return self.available


class TionTemperatureOutSensor(TionSensor):
    """Tion outside air flow temperature sensor."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize sensor device."""
        super().__init__(client, device)

        self._temperature = device.data.t_out

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device_guid}_temperature_out"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_name} Outside Air Flow Temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._temperature if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            self._temperature = self._device_data.data.t_out

        return self.available
