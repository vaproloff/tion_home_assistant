"""Support for Tion breezer."""

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    FAN_AUTO,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    MAJOR_VERSION,
    MINOR_VERSION,
    PRECISION_WHOLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo

from .client import TionClient, TionZoneDevice
from .const import (
    DOMAIN,
    SRVC_CONF_MAX_SPEED,
    SRVC_CONF_MIN_SPEED,
    SRVC_CONF_TARGET_CO2,
    Heater,
    SwingMode,
    TionDeviceType,
    ZoneMode,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up climate Tion entities."""
    tion_api: TionClient = hass.data[DOMAIN][entry.entry_id]

    devices = await tion_api.get_devices()
    entities = [
        TionClimate(tion_api, device)
        for device in devices
        if device.type in (TionDeviceType.BREEZER_3S, TionDeviceType.BREEZER_4S)
    ]

    async_add_entities(entities)

    platform = entity_platform.current_platform.get()
    assert platform

    platform.async_register_entity_service(
        "set_zone_target_co2",
        {
            vol.Optional(SRVC_CONF_TARGET_CO2): vol.Coerce(int),
        },
        "set_zone_target_co2",
    )

    platform.async_register_entity_service(
        "set_breezer_min_speed",
        {
            vol.Optional(SRVC_CONF_MIN_SPEED): vol.Coerce(int),
        },
        "set_breezer_min_speed",
    )

    platform.async_register_entity_service(
        "set_breezer_max_speed",
        {
            vol.Optional(SRVC_CONF_MAX_SPEED): vol.Coerce(int),
        },
        "set_breezer_max_speed",
    )

    return True


class TionClimate(ClimateEntity):
    """Tion climate devices,include air conditioner,heater."""

    _attr_translation_key = "tion_breezer"

    def __init__(self, client: TionClient, breezer: TionZoneDevice) -> None:
        """Initialize climate device for Tion Breezer."""
        self._api = client
        self._breezer_data = breezer

        self._breezer_guid = breezer.guid
        self._attr_name = breezer.name
        self._type = breezer.type
        self._attr_max_temp = breezer.t_max
        self._attr_min_temp = breezer.t_min
        self._breezer_valid = breezer.valid
        self._is_online = breezer.is_online
        self._is_on = breezer.data.is_on
        self._t_in = breezer.data.t_in
        self._t_out = breezer.data.t_out
        self._t_set = breezer.data.t_set
        self._heater_enabled = breezer.data.heater_enabled
        self._heater_mode = breezer.data.heater_mode
        self._heater_power = breezer.data.heater_power
        self._speed = breezer.data.speed
        self._speed_min_set = breezer.data.speed_min_set
        self._speed_max_set = breezer.data.speed_max_set
        self._gate = breezer.data.gate
        self._filter_time_seconds = breezer.data.filter_time_seconds
        self._filter_need_replace = breezer.data.filter_need_replace

        self._zone_guid = None
        self._zone_name = None
        self._mode = None
        self._target_co2 = None
        self._zone_valid = None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, breezer.guid)},
        )
        self._hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
        self._swing_modes = [SwingMode.SWING_OUTSIDE]

        self._fan_modes = [FAN_AUTO]
        self._fan_modes.extend(
            [str(speed) for speed in range(1, breezer.max_speed + 1)]
        )

        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.SWING_MODE
        )
        if breezer.data.heater_installed or breezer.data.heater_type is not None:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._hvac_modes.append(HVACMode.HEAT)

        if (MAJOR_VERSION, MINOR_VERSION) >= (2024, 2):
            self._enable_turn_on_off_backwards_compatibility = False
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._is_online and self._breezer_valid and self._zone_valid

    @property
    def name(self) -> str:
        """Return the name of the breezer."""
        return self._attr_name

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return self._breezer_guid

    @property
    def icon(self):
        """Return the entity picture to use in the frontend, if any."""
        return "mdi:air-filter"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Provides extra attributes."""
        attrs = {
            "mode": self.mode,
            "target_co2": self.target_co2,
            "speed": self.speed,
            "speed_min_set": self.speed_min_set,
            "speed_max_set": self.speed_max_set,
            "filter_days_left": self.filter_days_left,
            "filter_need_replace": self.filter_need_replace,
        }

        if self._heater_power is not None:
            attrs.update({"power": self._heater_power})

        return attrs

    @property
    def precision(self) -> int:
        """Return the precision of the system."""
        return PRECISION_WHOLE

    @property
    def target_temperature_step(self) -> int:
        """Return the supported step of target temperature."""
        return PRECISION_WHOLE

    @property
    def temperature_unit(self) -> UnitOfTemperature:
        """Return the unit of measurement used by the platform."""
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._attr_min_temp if self._breezer_valid else STATE_UNKNOWN

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._attr_max_temp if self._breezer_valid else STATE_UNKNOWN

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._t_out if self._breezer_valid else STATE_UNKNOWN

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._t_set if self._breezer_valid else STATE_UNKNOWN

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available operation modes."""
        return self._hvac_modes

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current operation."""
        if self._breezer_valid:
            if not self._is_on:
                return HVACMode.OFF

            if self.heater_enabled:
                return HVACMode.HEAT

            return HVACMode.FAN_ONLY

        return STATE_UNKNOWN

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation if supported."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if self._heater_power:
            return HVACAction.HEATING

        return HVACAction.FAN

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes."""
        return self._fan_modes

    @property
    def fan_mode(self) -> str:
        """Return the fan setting."""
        if self._mode == FAN_AUTO:
            return FAN_AUTO

        return str(self.speed)

    @property
    def swing_modes(self) -> list[SwingMode]:
        """Return the list of available preset modes."""
        return self._swing_modes

    @property
    def swing_mode(self) -> SwingMode | str:
        """Return current swing mode."""
        if self._type == TionDeviceType.BREEZER_4S:
            match self._gate:
                case 0:
                    return SwingMode.SWING_OUTSIDE
                case 1:
                    return SwingMode.SWING_INSIDE
        else:
            match self._gate:
                case 0:
                    return SwingMode.SWING_INSIDE
                case 1:
                    return SwingMode.SWING_MIXED
                case 2:
                    return SwingMode.SWING_OUTSIDE

        return STATE_UNKNOWN

    @property
    def mode(self) -> ZoneMode | str:
        """Return the current mode."""
        return self._mode if self._zone_valid else STATE_UNKNOWN

    @property
    def target_co2(self) -> int:
        """Return the current mode."""
        return self._target_co2 if self._zone_valid else STATE_UNKNOWN

    @property
    def speed(self) -> int:
        """Return the current speed."""
        try:
            return int(self._speed)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert breezer speed value to int: %s. Error: %s",
                self.name,
                self._speed,
                e,
            )

        return STATE_UNKNOWN

    @speed.setter
    def speed(self, new_speed: float) -> None:
        try:
            self._speed = float(new_speed)
        except ValueError as e:
            _LOGGER.warning(
                "%s: unable to convert new breezer speed value to float: %s. Error: %s",
                self.name,
                new_speed,
                e,
            )

    @property
    def heater_enabled(self) -> bool:
        """Return if heater active now."""
        if self._type == TionDeviceType.BREEZER_4S:
            return self._heater_mode == Heater.ON

        return self._heater_enabled if self._heater_enabled else False

    @heater_enabled.setter
    def heater_enabled(self, enabled: bool = False) -> None:
        if self._type == TionDeviceType.BREEZER_4S:
            self._heater_mode = Heater.ON if enabled else Heater.OFF
        else:
            self._heater_enabled = enabled

    @property
    def speed_min_set(self) -> int:
        """Return the minimum speed for auto mode."""
        return self._speed_min_set if self._breezer_valid else STATE_UNKNOWN

    @property
    def speed_max_set(self) -> int:
        """Return the maximum speed for auto mode."""
        return self._speed_max_set if self._breezer_valid else STATE_UNKNOWN

    @property
    def filter_days_left(self) -> str:
        """Return time left for filter replacement."""
        time_left = timedelta(seconds=self._filter_time_seconds)
        return f"{time_left.days}"

    @property
    def filter_need_replace(self) -> bool:
        """Return if filter need replace."""
        return self._filter_need_replace if self._breezer_valid else STATE_UNKNOWN

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await self._load_zone()
        self._set_swing_modes()
        await super().async_added_to_hass()

    async def async_turn_on(self) -> None:
        """Turn breezer on."""
        await self.async_set_hvac_mode(
            HVACMode.HEAT if self.heater_enabled else HVACMode.FAN_ONLY
        )

    async def async_turn_off(self) -> None:
        """Turn breezer off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        """Set new target operation mode."""
        if hvac_mode not in self._hvac_modes:
            _LOGGER.error("%s: unsupported hvac mode '%s'", self.name, hvac_mode)
            return

        if hvac_mode == self.hvac_mode:
            _LOGGER.info(
                "%s: no need to change HVAC mode: %s already set",
                self.name,
                hvac_mode,
            )
            return

        _LOGGER.info(
            "%s: changing HVAC mode (%s -> %s)", self.name, self.hvac_mode, hvac_mode
        )
        if hvac_mode == HVACMode.OFF:
            self._mode = ZoneMode.MANUAL
            self._is_on = False
        else:
            if hvac_mode == HVACMode.HEAT:
                self.heater_enabled = True
            elif hvac_mode == HVACMode.FAN_ONLY:
                self.heater_enabled = False

            if self.hvac_mode == HVACMode.OFF:
                self._is_on = True

        await self._send_breezer()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if self._attr_supported_features & ClimateEntityFeature.TARGET_TEMPERATURE:
            temperature = kwargs.get(ATTR_TEMPERATURE)

            if temperature is None:
                _LOGGER.warning("%s: undefined target temperature", self.name)
                return

            if temperature == self.target_temperature:
                _LOGGER.info(
                    "%s: no need to change target temperature: %s already set",
                    self.name,
                    temperature,
                )
                return

            self._t_set = int(temperature)
            await self._send_breezer()

        else:
            _LOGGER.warning("%s: service not supported", self.name)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        if fan_mode not in self._fan_modes:
            _LOGGER.error("%s: unsupported fan mode '%s'", self.name, fan_mode)
            return

        new_mode = ZoneMode.MANUAL
        new_speed = None

        if fan_mode == FAN_AUTO:
            new_mode = ZoneMode.AUTO
        else:
            try:
                new_speed = int(fan_mode)
            except ValueError as e:
                _LOGGER.warning(
                    "%s: unable to convert new fan mode to int: %s. Error: %s",
                    self.name,
                    fan_mode,
                    e,
                )

        if self._mode != new_mode:
            _LOGGER.info(
                "%s: changing zone mode (%s -> %s)",
                self.name,
                self._mode,
                new_mode,
            )
            self._mode = new_mode
            self._set_swing_modes()
            await self._send_zone()

        if (
            new_mode == ZoneMode.MANUAL
            and new_speed is not None
            and self.speed != new_speed
        ):
            _LOGGER.info(
                "%s: changing breezer speed (%s -> %s)",
                self.name,
                self.speed,
                new_speed,
            )
            self.speed = new_speed
            await self._send_breezer()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set Tion breezer air gate."""
        if swing_mode not in self._swing_modes:
            _LOGGER.info("%s: not supported swing mode %s", self.name, swing_mode)
            return

        new_gate = None
        match swing_mode:
            case SwingMode.SWING_OUTSIDE:
                new_gate = 0 if self._type == TionDeviceType.BREEZER_4S else 2
            case SwingMode.SWING_INSIDE:
                new_gate = 1 if self._type == TionDeviceType.BREEZER_4S else 0
            case SwingMode.SWING_MIXED:
                new_gate = 1 if self._type == TionDeviceType.BREEZER_3S else None

        if new_gate is not None and self._gate != new_gate:
            _LOGGER.info(
                "%s: changing gate (%s -> %s)",
                self.name,
                self.swing_mode,
                swing_mode,
            )
            self._gate = new_gate
            await self._send_breezer()

    def _set_swing_modes(self):
        self._swing_modes = [SwingMode.SWING_OUTSIDE]

        if self._mode == ZoneMode.MANUAL:
            self._swing_modes.append(SwingMode.SWING_INSIDE)
            if self._type == TionDeviceType.BREEZER_3S:
                self._swing_modes.append(SwingMode.SWING_MIXED)

    async def async_update(self):
        """Fetch new state data for the breezer.

        This is the only method that should fetch new data for Home Assistant.
        """
        await self._load_zone()
        await self._load_breezer()

    async def set_zone_target_co2(self, **kwargs):
        """Set zone new target co2 level."""
        new_target_co2 = kwargs.get(SRVC_CONF_TARGET_CO2)
        if new_target_co2 is not None and self._target_co2 != new_target_co2:
            _LOGGER.info(
                "%s: changing zone target co2 (%s -> %s)",
                self.name,
                self._target_co2,
                new_target_co2,
            )
            self._target_co2 = new_target_co2
            await self._send_zone()

    async def set_breezer_min_speed(self, **kwargs):
        """Set breezer new min speed."""
        new_min_speed = kwargs.get(SRVC_CONF_MIN_SPEED)

        if new_min_speed is not None and self._speed_min_set != new_min_speed:
            _LOGGER.info(
                "%s: changing breezer min speed (%s -> %s)",
                self.name,
                self._speed_min_set,
                new_min_speed,
            )
            self._speed_min_set = new_min_speed
            await self._send_breezer()

    async def set_breezer_max_speed(self, **kwargs):
        """Set breezer new min speed."""
        new_max_speed = kwargs.get(SRVC_CONF_MAX_SPEED)

        if new_max_speed is not None and self._speed_max_set != new_max_speed:
            _LOGGER.info(
                "%s: changing breezer max speed (%s -> %s)",
                self.name,
                self._speed_max_set,
                new_max_speed,
            )
            self._speed_max_set = new_max_speed
            await self._send_breezer()

    async def _load_breezer(self, force=False):
        """Update breezer data from API."""
        if device_data := await self._api.get_device(
            guid=self._breezer_guid, force=force
        ):
            self._attr_name = device_data.name
            self._breezer_guid = device_data.guid
            self._breezer_valid = device_data.data.data_valid
            self._is_on = device_data.data.is_on
            self._heater_enabled = device_data.data.heater_enabled
            self._heater_mode = device_data.data.heater_mode
            self._heater_power = device_data.data.heater_power
            self._t_set = device_data.data.t_set
            self._speed = device_data.data.speed
            self._speed_min_set = device_data.data.speed_min_set
            self._speed_max_set = device_data.data.speed_max_set
            self._gate = device_data.data.gate
            self._t_in = device_data.data.t_in
            self._t_out = device_data.data.t_out
            self._filter_time_seconds = device_data.data.filter_time_seconds
            self._filter_need_replace = device_data.data.filter_need_replace
            self._is_online = device_data.is_online

        _LOGGER.debug(
            "%s: fetching breezer data: valid=%s, is_on=%s, t_set=%s, t_in: %s, t_out: %s, speed=%s, speed_min_set=%s, speed_max_set=%s, heater_enabled=%s, heater_mode=%s, heater_power: %s, gate=%s, filter_time_seconds=%s, filter_need_replace: %s, is_online: %s",
            self.name,
            self._breezer_valid,
            self._is_on,
            self._t_set,
            self._t_in,
            self._t_out,
            self._speed,
            self._speed_min_set,
            self._speed_max_set,
            self._heater_enabled,
            self._heater_mode,
            self._heater_power,
            self._gate,
            self._filter_time_seconds,
            self._filter_need_replace,
            self._is_online,
        )

        return self.available

    async def _send_breezer(self) -> bool:
        """Send new breezer data to API."""
        if not self._breezer_valid:
            return False

        _LOGGER.debug(
            "%s: pushing new breezer data: is_on=%s, t_set=%s, speed=%s, speed_min_set=%s, speed_max_set=%s, heater_enabled=%s, heater_mode=%s, gate=%s",
            self.name,
            self._is_on,
            self._t_set,
            self.speed,
            self._speed_min_set,
            self._speed_max_set,
            self._heater_enabled,
            self._heater_mode,
            self._gate,
        )

        return await self._api.send_breezer(
            guid=self._breezer_guid,
            is_on=self._is_on,
            t_set=self._t_set,
            speed=self.speed,
            speed_min_set=self._speed_min_set,
            speed_max_set=self._speed_max_set,
            heater_enabled=self._heater_enabled,
            heater_mode=self._heater_mode,
            gate=self._gate,
        )

    async def _load_zone(self, force=False) -> bool:
        """Update zone data from API."""
        if zone_data := await self._api.get_device_zone(
            guid=self._breezer_guid, force=force
        ):
            old_mode = self._mode
            self._mode = zone_data.mode.current
            self._zone_guid = zone_data.guid
            self._zone_name = zone_data.name
            self._zone_valid = zone_data.valid

            if old_mode != self._mode:
                self._set_swing_modes()

            try:
                self._target_co2 = int(zone_data.mode.auto_set.co2)
            except ValueError as e:
                _LOGGER.warning(
                    "%s: unable to convert target CO2 value to int: %s. Error: %s",
                    self.name,
                    zone_data.mode.auto_set.co2,
                    e,
                )

            _LOGGER.debug(
                "%s: fetching zone data: name: %s, valid: %s, mode=%s, target_co2=%s",
                self.name,
                self._zone_name,
                self._zone_valid,
                self._mode,
                self._target_co2,
            )

        return self.available

    async def _send_zone(self) -> bool:
        """Send new zone data to API."""
        if not self._zone_valid:
            return False

        _LOGGER.debug(
            "%s: pushing new zone data: mode=%s, target_co2=%s",
            self.name,
            self._mode,
            self._target_co2,
        )

        return await self._api.send_zone(
            guid=self._zone_guid, mode=self.mode, co2=self._target_co2
        )
