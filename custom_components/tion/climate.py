"""Support for Tion breezer heater."""

from collections.abc import Mapping
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
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo

from .const import DOMAIN, SWING_INSIDE, SWING_MIXED, SWING_OUTSIDE
from .tion_api import Breezer, TionClient, TionZoneDevice

_LOGGER = logging.getLogger(__name__)


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
            if isinstance(device, Breezer):
                entities.append(TionClimate(device))

        else:
            _LOGGER.info("Skipped device %s, because of 'valid' property", device.name)

    async_add_entities(entities)

    platform = entity_platform.current_platform.get()
    assert platform

    platform.async_register_entity_service(
        "set_zone_target_co2",
        {
            vol.Optional("target_co2"): vol.Coerce(int),
        },
        "set_zone_target_co2",
    )

    platform.async_register_entity_service(
        "set_breezer_min_speed",
        {
            vol.Optional("min_speed"): vol.Coerce(int),
        },
        "set_breezer_min_speed",
    )

    platform.async_register_entity_service(
        "set_breezer_max_speed",
        {
            vol.Optional("max_speed"): vol.Coerce(int),
        },
        "set_breezer_max_speed",
    )

    return True


class TionClimate(ClimateEntity):
    """Tion climate devices,include air conditioner,heater."""

    _attr_translation_key = "tion_breezer"

    def __init__(self, breezer: Breezer) -> None:
        """Init climate device."""
        self._breezer: Breezer = breezer
        self._hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]

        self._swing_modes = [SWING_OUTSIDE, SWING_INSIDE]
        if self._breezer != "breezer4":
            self._swing_modes.append(SWING_MIXED)

        self._fan_modes = [FAN_AUTO]
        self._fan_modes.extend(
            [str(speed) for speed in range(1, self._breezer.max_speed + 1)]
        )

        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.SWING_MODE
        )
        if self._breezer.heater_installed:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._hvac_modes.append(HVACMode.HEAT)

        if (MAJOR_VERSION, MINOR_VERSION) >= (2024, 2):
            self._enable_turn_on_off_backwards_compatibility = False
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON

    @property
    def device_info(self) -> DeviceInfo:
        """Link entity to the device."""
        return DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, self._breezer.mac)},
            identifiers={(DOMAIN, self._breezer.guid)},
            manufacturer="Tion",
            model_id=self._breezer.type,
            name=self._breezer.name,
            suggested_area=self._breezer.zone.name,
            sw_version=self._breezer.firmware,
            hw_version=self._breezer.hardware,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._breezer.valid and self._breezer.zone.valid

    @property
    def name(self) -> str:
        """Return the name of the breezer."""
        return self._breezer.name

    @property
    def unique_id(self):
        """Return a unique id identifying the entity."""
        return self._breezer.guid

    @property
    def icon(self):
        """Return the entity picture to use in the frontend, if any."""
        return "mdi:air-filter"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Provides extra attributes."""
        return {
            "mode": self.mode,
            "target_co2": self.target_co2,
            "speed": self.speed,
            "speed_min_set": self.speed_min_set,
            "speed_max_set": self.speed_max_set,
            "filter_need_replace": self.filter_need_replace,
        }

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
        return self._breezer.t_min if self._breezer.valid else STATE_UNKNOWN

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._breezer.t_max if self._breezer.valid else STATE_UNKNOWN

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._breezer.t_out if self._breezer.valid else STATE_UNKNOWN

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._breezer.t_set if self._breezer.valid else STATE_UNKNOWN

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available operation modes."""
        return self._hvac_modes

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current operation."""
        if self._breezer.valid:
            if not self._breezer.is_on:
                return HVACMode.OFF

            if self._breezer.heater_enabled:
                return HVACMode.HEAT

            return HVACMode.FAN_ONLY

        return STATE_UNKNOWN

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation if supported."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if self._breezer.heater_power:
            return HVACAction.HEATING

        return HVACAction.FAN

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return self._fan_modes

    @property
    def fan_mode(self):
        """Return the fan setting."""
        if self._breezer.zone.mode == FAN_AUTO:
            return FAN_AUTO

        return str(int(self._breezer.speed))

    @property
    def swing_modes(self):
        """Return the list of available preset modes."""
        return self._swing_modes

    @property
    def swing_mode(self):
        """Return current swing mode."""
        if self._breezer.gate == 0:
            return SWING_OUTSIDE

        if self._breezer.gate == 1:
            if self._breezer.type == "breezer4":
                return SWING_INSIDE

            return SWING_MIXED

        if self._breezer.gate == 2:
            return SWING_INSIDE

        return STATE_UNKNOWN

    @property
    def mode(self) -> str:
        """Return the current mode."""
        return self._breezer.zone.mode if self._breezer.zone.valid else STATE_UNKNOWN

    @property
    def target_co2(self) -> str:
        """Return the current mode."""
        return (
            self._breezer.zone.target_co2 if self._breezer.zone.valid else STATE_UNKNOWN
        )

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
        """Return if filter need replace."""
        return (
            self._breezer.filter_need_replace if self._breezer.valid else STATE_UNKNOWN
        )

    def turn_on(self) -> None:
        """Turn breezer on."""
        if self._breezer.heater_enabled:
            self.set_hvac_mode(HVACMode.HEAT)
        else:
            self.set_hvac_mode(HVACMode.FAN_ONLY)

    def turn_off(self) -> None:
        """Turn breezer off."""
        self.set_hvac_mode(HVACMode.OFF)

    def set_hvac_mode(self, hvac_mode) -> None:
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
            self._breezer.is_on = False
        else:
            if hvac_mode == HVACMode.HEAT:
                self._breezer.heater_enabled = True
            elif hvac_mode == HVACMode.FAN_ONLY:
                self._breezer.heater_enabled = False

            if self.hvac_mode == HVACMode.OFF:
                self._breezer.is_on = True

        self._breezer.send()

    def set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if self._attr_supported_features & ClimateEntityFeature.TARGET_TEMPERATURE:
            temperature = kwargs.get(ATTR_TEMPERATURE)

            if temperature is None:
                _LOGGER.warning("%s: undefined target temperature", self.name)
                return

            self._breezer.t_set = int(temperature)
            self._breezer.send()

        else:
            _LOGGER.warning("%s: service not supported", self.name)

    def set_fan_mode(self, fan_mode) -> None:
        """Set new target fan mode."""
        if fan_mode not in self._fan_modes:
            _LOGGER.error("%s: unsupported fan mode '%s'", self.name, fan_mode)
            return

        new_mode = "manual"
        new_speed = None

        if fan_mode == FAN_AUTO:
            new_mode = FAN_AUTO
        else:
            new_speed = int(fan_mode)

        if self._breezer.zone.mode != new_mode:
            _LOGGER.info(
                "%s: changing zone mode (%s -> %s)",
                self.name,
                self._breezer.zone.mode,
                new_mode,
            )
            self._breezer.zone.mode = new_mode
            self._breezer.zone.send()

        if (
            new_mode == "manual"
            and new_speed is not None
            and self._breezer.speed != new_speed
        ):
            _LOGGER.info(
                "%s: changing breezer speed (%s -> %s)",
                self.name,
                self._breezer.speed,
                new_speed,
            )
            self._breezer.speed = new_speed
            self._breezer.send()

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set Tion breezer air gate."""
        new_gate = 1
        if swing_mode == SWING_OUTSIDE:
            new_gate = 0
        elif swing_mode == SWING_INSIDE and self._breezer.type != "breezer4":
            new_gate = 2

        if self._breezer.gate != new_gate:
            _LOGGER.info(
                "%s: changing gate (%s -> %s)",
                self._breezer.name,
                self.swing_mode,
                swing_mode,
            )
            self._breezer.gate = new_gate
            self._breezer.send()

    def update(self):
        """Fetch new state data for the breezer.

        This is the only method that should fetch new data for Home Assistant.
        """
        self._breezer.zone.load()
        self._breezer.load()

    def set_zone_target_co2(self, **kwargs):
        """Set zone new target co2 level."""
        new_target_co2 = kwargs.get("target_co2")
        if (
            new_target_co2 is not None
            and self._breezer.zone.target_co2 != new_target_co2
        ):
            _LOGGER.info(
                "%s: changing zone target co2 (%s -> %s)",
                self.name,
                self._breezer.zone.target_co2,
                new_target_co2,
            )
            self._breezer.zone.target_co2 = new_target_co2
            self._breezer.zone.send()

    def set_breezer_min_speed(self, **kwargs):
        """Set breezer new min speed."""
        new_min_speed = kwargs.get("min_speed")

        if new_min_speed is not None and self._breezer.speed_min_set != new_min_speed:
            _LOGGER.info(
                "%s: changing breezer min speed (%s -> %s)",
                self.name,
                self._breezer.speed_min_set,
                new_min_speed,
            )
            self._breezer.speed_min_set = new_min_speed
            self._breezer.send()

    def set_breezer_max_speed(self, **kwargs):
        """Set breezer new min speed."""
        new_max_speed = kwargs.get("max_speed")

        if new_max_speed is not None and self._breezer.speed_max_set != new_max_speed:
            _LOGGER.info(
                "%s: changing breezer max speed (%s -> %s)",
                self.name,
                self._breezer.speed_max_set,
                new_max_speed,
            )
            self._breezer.speed_max_set = new_max_speed
            self._breezer.send()
