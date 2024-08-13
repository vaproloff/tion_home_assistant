"""Platform for sensor integration."""

import logging

from homeassistant.components.sensor import (
    ATTR_STATE_CLASS as STATE_CLASS,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo

from .const import CO2_PPM, DOMAIN, HUM_PERCENT
from .tion_api import Breezer, MagicAir, TionClient, TionZoneDevice

_LOGGER = logging.getLogger(__name__)

# Sensor types
CO2_SENSOR = {
    "native_unit_of_measurement": CO2_PPM,
    "name": "co2",
    STATE_CLASS: SensorStateClass.MEASUREMENT,
    "device_class": SensorDeviceClass.CO2,
    "suggested_display_precision": 0,
}
TEMP_SENSOR = {
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "name": "temperature",
    STATE_CLASS: SensorStateClass.MEASUREMENT,
    "device_class": SensorDeviceClass.TEMPERATURE,
    "suggested_display_precision": 0,
}
HUM_SENSOR = {
    "native_unit_of_measurement": HUM_PERCENT,
    "name": "humidity",
    STATE_CLASS: SensorStateClass.MEASUREMENT,
    "device_class": SensorDeviceClass.HUMIDITY,
    "suggested_display_precision": 0,
}
TEMP_IN_SENSOR = {
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "name": "temperature in",
    STATE_CLASS: SensorStateClass.MEASUREMENT,
    "device_class": SensorDeviceClass.TEMPERATURE,
    "suggested_display_precision": 0,
}
TEMP_OUT_SENSOR = {
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "name": "temperature out",
    STATE_CLASS: SensorStateClass.MEASUREMENT,
    "device_class": SensorDeviceClass.TEMPERATURE,
    "suggested_display_precision": 0,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up climate Tion entities."""
    tion_api: TionClient = hass.data[DOMAIN][entry.entry_id]

    entities = []
    devices = await hass.async_add_executor_job(tion_api.get_devices)
    device: TionZoneDevice
    for device in devices:
        if device.valid:
            if isinstance(device, MagicAir):
                entities.append(TionSensor(device, CO2_SENSOR))
                entities.append(TionSensor(device, TEMP_SENSOR))
                entities.append(TionSensor(device, HUM_SENSOR))
            elif isinstance(device, Breezer):
                entities.append(TionSensor(device, TEMP_IN_SENSOR))
                entities.append(TionSensor(device, TEMP_OUT_SENSOR))
        else:
            _LOGGER.info("Skipped device %s, because of 'valid' property", device)

    async_add_entities(entities)
    return True


class TionSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(self, device: Breezer | MagicAir, sensor_type) -> None:
        """Initialize sensor device."""
        self._device = device
        self._sensor_type = sensor_type

        if sensor_type.get(STATE_CLASS, None) is not None:
            self._attr_state_class = sensor_type[STATE_CLASS]
        if sensor_type.get("device_class", None) is not None:
            self._attr_device_class = sensor_type["device_class"]
        if sensor_type.get("native_unit_of_measurement", None) is not None:
            self._attr_native_unit_of_measurement = sensor_type[
                "native_unit_of_measurement"
            ]
        if sensor_type.get("suggested_display_precision", None) is not None:
            self._attr_suggested_display_precision = sensor_type[
                "suggested_display_precision"
            ]

    @property
    def device_info(self) -> DeviceInfo:
        """Link entity to the device."""
        return DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, self._device.mac)},
            identifiers={(DOMAIN, self._device.guid)},
            manufacturer="Tion",
            model_id=self._device.type,
            name=self._device.name,
            suggested_area=self._device.zone.name,
            sw_version=self._device.firmware,
            hw_version=self._device.hardware,
        )

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return self._device.guid + self._sensor_type["name"]

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device.name} {self._sensor_type['name']}"

    @property
    def state(self):
        """Return the state of the sensor."""
        state = STATE_UNKNOWN
        if self._sensor_type == CO2_SENSOR:
            state = self._device.co2
        elif self._sensor_type == TEMP_SENSOR:
            state = self._device.temperature
        elif self._sensor_type == HUM_SENSOR:
            state = self._device.humidity
        elif self._sensor_type == TEMP_IN_SENSOR:
            state = self._device.t_in
        elif self._sensor_type == TEMP_OUT_SENSOR:
            state = self._device.t_out
        return state if self._device.valid else STATE_UNKNOWN

    def update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        self._device.load()
