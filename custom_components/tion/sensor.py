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
    """Set up sensor Tion entities."""
    client: TionClient = hass.data[DOMAIN][entry.entry_id]

    entities = []
    devices = await client.get_devices()
    for device in devices:
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
        self._device = device
        self._device_data = device

        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.guid)},
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device.is_online and self._device.valid

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

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await self._load()
        await super().async_added_to_hass()

    async def async_update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        await self._load()

    async def _load(self, force=False) -> bool:
        if device_data := await self._api.get_device(
            guid=self._device.guid, force=force
        ):
            self._device = device_data
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

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_temperature"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} Temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.data.temperature if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            _LOGGER.debug(
                "%s: fetched data: temperature=%s",
                self.name,
                self._device.data.temperature,
            )

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

        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_humidity"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} Humidity"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.data.humidity if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            _LOGGER.debug(
                "%s: fetched data: humidity=%s",
                self.name,
                self._device.data.humidity,
            )

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

        self._attr_device_class = SensorDeviceClass.CO2
        self._attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_co2"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} CO2"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.data.co2 if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            _LOGGER.debug(
                "%s: fetched data: co2=%s",
                self.name,
                self._device.data.co2,
            )

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

        self._attr_device_class = SensorDeviceClass.PM25
        self._attr_native_unit_of_measurement = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_pm25"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} PM25"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.data.pm25 if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            _LOGGER.debug(
                "%s: fetched data: pm25=%s",
                self.name,
                self._device.data.pm25,
            )

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

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_temperature_in"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} Inflow Temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.data.t_in if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            _LOGGER.debug(
                "%s: fetched data: temperature_in=%s",
                self.name,
                self._device.data.t_in,
            )

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

        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_temperature_out"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} Outflow Temperature"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._device.data.t_out if self.available else STATE_UNKNOWN

    async def _load(self, force=False):
        """Update device data from API."""
        if await super()._load(force=force):
            _LOGGER.debug(
                "%s: fetched data: temperature_out=%s",
                self.name,
                self._device.data.t_out,
            )

        return self.available
