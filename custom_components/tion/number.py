"""Platform for number integration."""

import abc
import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .client import TionClient, TionZone, TionZoneDevice
from .const import DOMAIN, TionDeviceType

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up switch Tion entities."""
    client: TionClient = hass.data[DOMAIN][entry.entry_id]

    entities = []
    devices = await client.get_devices()
    for device in devices:
        if device.valid:
            if device.type in [
                TionDeviceType.BREEZER_3S,
                TionDeviceType.BREEZER_4S,
            ]:
                entities.append(TionMinSpeed(client, device))
                entities.append(TionMaxSpeed(client, device))
            elif device.type in [
                TionDeviceType.MAGIC_AIR,
                TionDeviceType.MODULE_CO2,
            ]:
                entities.append(TionTargetCO2(client, device))

        else:
            _LOGGER.info("Skipped device %s (not valid)", device.name)

    async_add_entities(entities)
    return True


class TionNumber(NumberEntity, abc.ABC):
    """Abstract Tion switch."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        self._api = client
        self._device = device

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.guid)},
        )

        self._attr_mode = NumberMode.SLIDER

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device.is_online and self._device.valid

    @property
    @abc.abstractmethod
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the entity name."""

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await self._load()
        await super().async_added_to_hass()

    @abc.abstractmethod
    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""

    async def async_update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        await self._load()

    async def _load(self, force=False) -> bool:
        """Update device data from API."""
        if device_data := await self._api.get_device(
            guid=self._device.guid, force=force
        ):
            self._device = device_data
            return True

        return False

    @abc.abstractmethod
    async def _send(self) -> None:
        """Push new data to API."""


class TionTargetCO2(TionNumber):
    """Tion Target CO2 Level Number."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(client, device)

        self._zone: TionZone | None = None

        self._attr_device_class = NumberDeviceClass.CO2
        self._attr_native_min_value = 550
        self._attr_native_max_value = 1500
        self._attr_native_step = 10

        self._target_co2: float = None

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_target_co2"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device.name} Target CO2"

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the number."""
        return (
            self._target_co2
            if self._zone.valid and self._target_co2 is not None
            else STATE_UNKNOWN
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        await self._load()
        self._target_co2 = value
        await self._send()

    async def _load(self, force=False) -> bool:
        if await super()._load(force=force):
            if zone_data := await self._api.get_device_zone(
                guid=self._device.guid, force=force
            ):
                self._zone = zone_data

                try:
                    self._target_co2 = float(zone_data.mode.auto_set.co2)
                except ValueError as e:
                    _LOGGER.warning(
                        "%s: unable to convert target CO2 value to float: %s. Error: %s",
                        self.name,
                        zone_data.mode.auto_set.co2,
                        e,
                    )

        return self.available

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            return False

        try:
            target_co2 = int(self._target_co2)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert target CO2 value to int: %s. Error: %s",
                self.name,
                self._target_co2,
                e,
            )
            return False

        _LOGGER.debug(
            "%s: pushing new zone data: mode=%s, target_co2=%s",
            self.name,
            self._zone.mode.current,
            target_co2,
        )

        return await self._api.send_zone(
            guid=self._zone.guid, mode=self._zone.mode.current, co2=target_co2
        )


class TionMinSpeed(TionNumber):
    """Tion Minimum Speed Number for Breezer Auto Mode."""

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(client, device)

        self._attr_native_min_value = 0
        self._attr_native_max_value = 6
        self._attr_native_step = 1

        self._breezer_min_speed: float = None

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_min_speed_set"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device.name} Min Speed Set"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:fan-chevron-down"

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the number."""
        return (
            self._breezer_min_speed
            if self._device.valid and self._breezer_min_speed is not None
            else STATE_UNKNOWN
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        await self._load()
        self._breezer_min_speed = value
        await self._send()

    async def _load(self, force=False) -> bool:
        if await super()._load(force=force):
            try:
                self._breezer_min_speed = float(self._device.data.speed_min_set)
            except ValueError as e:
                _LOGGER.warning(
                    "%s: unable to convert breezer min speed set value to float: %s. Error: %s",
                    self.name,
                    self._device.data.speed_min_set,
                    e,
                )

        return self.available

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            return

        try:
            breezer_min_speed = int(self._breezer_min_speed)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer min speed set value to int: %s. Error: %s",
                self.name,
                self._breezer_min_speed,
                e,
            )
            return

        try:
            breezer_t_set = int(self._device.data.t_set)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer temperature set value to int: %s. Error: %s",
                self.name,
                self._device.data.t_set,
                e,
            )
            return

        try:
            breezer_speed = int(self._device.data.speed)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer speed value to int: %s. Error: %s",
                self.name,
                self._device.data.speed,
                e,
            )
            return

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

        await self._api.send_breezer(
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

    def __init__(
        self,
        client: TionClient,
        device: TionZoneDevice,
    ) -> None:
        """Initialize switch device."""
        super().__init__(client, device)

        self._attr_native_min_value = 0
        self._attr_native_max_value = 6
        self._attr_native_step = 1

        self._breezer_max_speed: float = None

    @property
    def unique_id(self) -> str:
        """Return a unique id identifying the entity."""
        return f"{self._device.guid}_max_speed_set"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{self._device.name} Max Speed Set"

    @property
    def icon(self) -> str:
        """Return the MDI icon."""
        return "mdi:fan-chevron-up"

    @property
    def native_value(self) -> int | None:
        """Return the value reported by the number."""
        return (
            self._breezer_max_speed
            if self._device.valid and self._breezer_max_speed is not None
            else STATE_UNKNOWN
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        await self._load()
        self._breezer_max_speed = value
        await self._send()

    async def _load(self, force=False) -> bool:
        if await super()._load(force=force):
            try:
                self._breezer_max_speed = float(self._device.data.speed_max_set)
            except ValueError as e:
                _LOGGER.warning(
                    "%s: unable to convert breezer max speed set value to float: %s. Error: %s",
                    self.name,
                    self._device.data.speed_min_set,
                    e,
                )

        return self.available

    async def _send(self) -> None:
        """Send new switch data to API."""
        if not self.available:
            return

        try:
            breezer_max_speed = int(self._breezer_max_speed)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer max speed set value to int: %s. Error: %s",
                self.name,
                self._breezer_max_speed,
                e,
            )
            return

        try:
            breezer_t_set = int(self._device.data.t_set)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer temperature set value to int: %s. Error: %s",
                self.name,
                self._device.data.t_set,
                e,
            )
            return

        try:
            breezer_speed = int(self._device.data.speed)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer speed value to int: %s. Error: %s",
                self.name,
                self._device.data.speed,
                e,
            )
            return

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

        await self._api.send_breezer(
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
