"""Support for Tion breezer heater."""

import logging

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    FAN_AUTO,
    FAN_OFF,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    MAJOR_VERSION,
    MINOR_VERSION,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from tion import Breezer, Zone

from .const import (
    LAST_FAN_SPEED_SYNCED,
    SWING_INSIDE,
    SWING_MIXED,
    SWING_OUTSIDE,
    TION_API,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    """Set up Tion climate platform."""
    tion = hass.data[TION_API]
    if discovery_info is None:
        return
    devices = [TionClimate(tion, device["guid"]) for device in discovery_info]

    async_add_entities(devices)


class TionClimate(ClimateEntity):
    """Tion climate devices,include air conditioner,heater."""

    _attr_translation_key = "tion_breezer"

    def __init__(self, tion, guid) -> None:
        """Init climate device."""
        self._breezer: Breezer = tion.get_devices(guid=guid)[0]
        self._zone: Zone = tion.get_zones(guid=self._breezer.zone.guid)[0]
        self._last_fan_speed_synced = None

        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.SWING_MODE
        )
        if self._breezer.heater_installed:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE

        if (MAJOR_VERSION, MINOR_VERSION) >= (2024, 2):
            self._enable_turn_on_off_backwards_compatibility = False
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON

    @property
    def temperature_unit(self):
        """Return the unit of measurement used by the platform."""
        return UnitOfTemperature.CELSIUS

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return self._breezer.guid

    @property
    def name(self):
        """Return the name of the breezer."""
        return f"{self._breezer.name}"

    @property
    def type(self):
        """Return the type of the breezer."""
        return "breezer4" if "4S" in self.name else "breezer3"

    @property
    def hvac_mode(self):
        """Return current operation."""
        if self._breezer.valid:
            if self._zone.mode == "manual" and not self._breezer.is_on:
                return HVACMode.OFF

            if self._breezer.heater_enabled:
                return HVACMode.HEAT

            return HVACMode.FAN_ONLY

        return STATE_UNKNOWN

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        _operations = [HVACMode.OFF, HVACMode.FAN_ONLY]
        if self._breezer.heater_installed:
            _operations.append(HVACMode.HEAT)
        return _operations

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._breezer.t_out if self._breezer.valid else STATE_UNKNOWN

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._breezer.t_set if self._breezer.valid else STATE_UNKNOWN

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 1

    @property
    def fan_mode(self):
        """Return the fan setting."""
        if self._zone.mode == "auto":
            return FAN_AUTO

        if not self._breezer.is_on:
            return FAN_OFF

        fan_speed = str(int(self._breezer.speed))
        self._last_fan_speed_synced = fan_speed

        return fan_speed

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        _fan_modes = [FAN_OFF, FAN_AUTO]
        try:
            _fan_modes.extend(range(int(self._breezer.speed_limit) + 1))
        except AttributeError:
            _fan_modes.extend(range(7))
            _LOGGER.info(
                "Breezer.speed_limit is %s, fan_modes set to 0-6",
                self._breezer.speed_limit,
            )
        return [str(m) for m in _fan_modes]

    @property
    def swing_mode(self):
        """Return the swing mode. It's 3 type: inside, outside, mixed."""
        if self._breezer.gate == 0:
            _swing_mode = SWING_OUTSIDE
        elif self._breezer.gate == 1:
            if self.type == "breezer4":
                _swing_mode = SWING_INSIDE
            else:
                _swing_mode = SWING_MIXED
        elif self._breezer.gate == 2:
            _swing_mode = SWING_INSIDE
        else:
            _swing_mode = STATE_UNKNOWN

        return _swing_mode

    @property
    def swing_modes(self):
        """Return the list of available preset modes."""
        _swing_modes = [SWING_OUTSIDE, SWING_INSIDE]
        if self.type != "breezer4":
            _swing_modes.append(SWING_MIXED)

        return [str(m) for m in _swing_modes]

    def turn_on(self) -> None:
        """Turn breezer on."""
        if self._breezer.heater_enabled and self._breezer.heater_installed:
            self.set_hvac_mode(HVACMode.HEAT)
        else:
            self.set_hvac_mode(HVACMode.FAN_ONLY)

    def turn_off(self) -> None:
        """Turn breezer off."""
        self.set_hvac_mode(HVACMode.OFF)

    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        if ATTR_TEMPERATURE in kwargs:
            self._breezer.t_set = int(kwargs[ATTR_TEMPERATURE])
            self._breezer.send()
        if ATTR_HVAC_MODE in kwargs:
            self.set_hvac_mode(kwargs[ATTR_HVAC_MODE])

    def set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        new_mode = "manual"
        new_speed = None
        if fan_mode == FAN_AUTO:
            new_mode = FAN_AUTO
        elif fan_mode == FAN_OFF:
            new_speed = 0
        elif fan_mode.isdigit():  # 1-6
            new_speed = int(fan_mode)
        if self._zone.mode != new_mode:
            _LOGGER.info("Setting zone mode to %s", new_mode)
            self._zone.mode = new_mode
            self._zone.send()
        if new_mode == "manual" and new_speed is not None:
            _LOGGER.info("Setting breezer fan_mode to %s", new_speed)
            self._breezer.speed = new_speed
            self._breezer.send()

    def set_hvac_mode(self, hvac_mode):
        """Set new target operation mode."""
        _LOGGER.info("Setting hvac mode to %s", hvac_mode)
        if hvac_mode == HVACMode.OFF:
            self.set_fan_mode(FAN_OFF)
        else:
            if hvac_mode == HVACMode.HEAT:
                self._breezer.heater_enabled = True
                self._breezer.send()
            elif hvac_mode == HVACMode.FAN_ONLY:
                self._breezer.heater_enabled = False
                self._breezer.send()
            if self.hvac_mode == HVACMode.OFF:
                self.set_fan_mode(
                    self._last_fan_speed_synced
                    if self._last_fan_speed_synced is not None
                    else "1"
                )

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set Tion breezer air gate."""
        if swing_mode == SWING_OUTSIDE:
            self._breezer.gate = 0
        elif swing_mode == SWING_MIXED:
            self._breezer.gate = 1
        elif swing_mode == SWING_INSIDE:
            self._breezer.gate = 1 if self.type == "breezer4" else 2
        else:
            self._breezer.gate = 1
        _LOGGER.info(
            "Device: %s Swing mode changed to %s", self._breezer.name, swing_mode
        )

        if self.hvac_mode != HVACMode.OFF:
            self.set_fan_mode(
                self._last_fan_speed_synced
                if self._last_fan_speed_synced is not None
                else "1"
            )
        self._breezer.send()
        self._breezer.load()

    def update(self):
        """Fetch new state data for the breezer.

        This is the only method that should fetch new data for Home Assistant.
        """
        self._zone.load()
        self._breezer.load()

    @property
    def mode(self) -> str:
        """Return the current mode."""
        return self._zone.mode if self._zone.valid else STATE_UNKNOWN

    @property
    def target_co2(self) -> str:
        """Return the current mode."""
        return self._zone.target_co2 if self._zone.valid else STATE_UNKNOWN

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._breezer.t_min if self._breezer.valid else STATE_UNKNOWN

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._breezer.t_max if self._breezer.valid else STATE_UNKNOWN

    @property
    def speed(self) -> str:
        """Return the current speed."""
        return self._breezer.speed if self._breezer.valid else STATE_UNKNOWN

    @property
    def speed_min_set(self) -> str:
        """Return the minimum speed for auto mode."""
        return self._breezer.speed_min_set if self._breezer.valid else STATE_UNKNOWN

    @property
    def speed_max_set(self) -> str:
        """Return the maximum speed for auto mode."""
        return self._breezer.speed_max_set if self._breezer.valid else STATE_UNKNOWN

    @property
    def filter_need_replace(self) -> str:
        """Return filter_need_replace input_boolean."""
        return (
            self._breezer.filter_need_replace if self._breezer.valid else STATE_UNKNOWN
        )

    @property
    def t_in(self) -> str:
        """Return filter_need_replace input_boolean."""
        return self._breezer.t_in if self._breezer.valid else STATE_UNKNOWN

    @property
    def state_attributes(self) -> dict:
        """Return optional state attributes."""
        data = super().state_attributes
        data["mode"] = self.mode
        data["target_co2"] = self.target_co2
        data["speed"] = self.speed
        data["speed_min_set"] = self.speed_min_set
        data["speed_max_set"] = self.speed_max_set
        data["filter_need_replace"] = self.filter_need_replace
        data["t_in"] = self.t_in
        data[LAST_FAN_SPEED_SYNCED] = self._last_fan_speed_synced
        return data

    @property
    def icon(self):
        """Return the entity picture to use in the frontend, if any."""
        return "mdi:air-filter"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._breezer.valid and self._zone.valid
