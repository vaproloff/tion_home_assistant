"""Climate entity of Tion breezers."""

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    FAN_AUTO,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Breezer, Flap
from .const import SwingMode
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, device_views

SWING_MODES = {
    Flap.OUTSIDE: SwingMode.SWING_OUTSIDE,
    Flap.INSIDE: SwingMode.SWING_INSIDE,
    Flap.MIXED: SwingMode.SWING_MIXED,
}
FLAPS = {swing_mode: flap for flap, swing_mode in SWING_MODES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity for each breezer."""
    coordinator = entry.runtime_data
    async_add_entities(
        TionClimate(coordinator, device_view)
        for device_view in device_views(coordinator)
        if isinstance(device_view, Breezer)
    )


class TionClimate(TionEntity[Breezer], ClimateEntity):
    """A breezer: power, fan speed, heating and the air flap."""

    _attr_name = None
    _attr_translation_key = "tion_breezer"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: TionCoordinator, breezer: Breezer) -> None:
        """Create the climate entity of one breezer."""
        super().__init__(coordinator, breezer, None)

    @property
    def _heater_installed(self) -> bool:
        return self.device_view.heater_installed is True

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the features the breezer has now."""
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self._heater_installed:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self.device_view.flap_modes:
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    def min_temp(self) -> float:
        """Return the lowest target temperature."""
        return self.device_view.target_temperature_range[0]

    @property
    def max_temp(self) -> float:
        """Return the highest target temperature."""
        return self.device_view.target_temperature_range[1]

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature of the air the breezer blows in."""
        return self.device_view.temperature_outlet

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature of the blown-in air."""
        return self.device_view.target_temperature

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the modes the breezer has."""
        modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
        if self._heater_installed:
            modes.append(HVACMode.HEAT)
        return modes

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return off, fan only, or heat when the heater is allowed."""
        breezer = self.device_view
        if breezer.is_on is None:
            return None
        if not breezer.is_on:
            return HVACMode.OFF
        if self._heater_installed and breezer.heater_enabled:
            return HVACMode.HEAT
        return HVACMode.FAN_ONLY

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return what the breezer is doing."""
        breezer = self.device_view
        if breezer.is_on is None:
            return None
        if not breezer.is_on:
            return HVACAction.OFF
        # Only the 4S reports heater power; the others heat whenever allowed.
        if breezer.supports("heater_power"):
            heating = (breezer.heater_power or 0) > 0
        else:
            heating = self.hvac_mode is HVACMode.HEAT
        return HVACAction.HEATING if heating else HVACAction.FAN

    @property
    def fan_modes(self) -> list[str]:
        """Return the speeds, and auto when the room has auto mode."""
        speeds = [
            str(speed) for speed in range(1, (self.device_view.speed_max or 0) + 1)
        ]
        return [FAN_AUTO, *speeds] if self.room_auto is not None else speeds

    @property
    def fan_mode(self) -> str | None:
        """Return auto while the room's auto mode is on, else the speed."""
        if (auto := self.room_auto) is not None and auto.enabled:
            return FAN_AUTO
        breezer = self.device_view
        if breezer.is_on and breezer.speed is not None and breezer.speed >= 1:
            return str(breezer.speed)
        return None

    @property
    def swing_modes(self) -> list[str] | None:
        """Return where the breezer can take air from."""
        return [SWING_MODES[flap] for flap in self.device_view.flap_modes] or None

    @property
    def swing_mode(self) -> str | None:
        """Return where the breezer takes air from."""
        flap = self.device_view.flap
        return SWING_MODES[flap] if flap is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the room's mode, the speed (0 when off) and the heater power."""
        breezer = self.device_view
        auto = self.room_auto
        attributes: dict[str, Any] = {
            "mode": None if auto is None else ("auto" if auto.enabled else "manual"),
            "speed": breezer.speed if breezer.is_on else 0,
        }
        if breezer.heater_power is not None:
            attributes["power"] = breezer.heater_power
        return attributes

    async def async_turn_on(self) -> None:
        """Turn the breezer on."""
        await self.async_send_command(is_on=True)

    async def async_turn_off(self) -> None:
        """Turn the breezer off."""
        await self._async_power_off()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the breezer off, or on with the heater allowed or not."""
        if hvac_mode is HVACMode.OFF:
            await self._async_power_off()
            return
        changes: dict[str, bool] = {"is_on": True}
        if self._heater_installed:
            changes["heater_enabled"] = hvac_mode is HVACMode.HEAT
        await self.async_send_command(**changes)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature, after the mode if one is given."""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_handle_set_hvac_mode_service(hvac_mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.async_send_command(target_temperature=temperature)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Hand the speed to the room's auto mode, or set it by hand."""
        if fan_mode == FAN_AUTO:
            await self.async_set_room_auto(enabled=True)
            return
        await self._async_leave_auto()
        await self.async_send_command(speed=int(fan_mode))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set where the breezer takes air from."""
        await self.async_send_command(flap=FLAPS[SwingMode(swing_mode)])

    async def _async_power_off(self) -> None:
        # The room's auto mode would turn the breezer back on.
        await self._async_leave_auto()
        await self.async_send_command(is_on=False)

    async def _async_leave_auto(self) -> None:
        # Before a manual change, or the auto mode overrides it.
        if (auto := self.room_auto) is not None and auto.enabled:
            await self.async_set_room_auto(enabled=False)
