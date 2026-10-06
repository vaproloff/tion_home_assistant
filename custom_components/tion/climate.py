"""Climate entity of Tion breezers."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Self

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    FAN_AUTO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .api import Breezer, Flap
from .const import CONF_PRESETS, FAN_LOCAL_PID, SwingMode
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, device_views
from .pid_manager import TionPidManager
from .presets import (
    Baseline,
    ManualPreset,
    PidBaseline,
    PidPreset,
    Preset,
    SpeedBaseline,
    TionPresetController,
    baseline_from_storage,
    baseline_to_storage,
    visible_presets,
)

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


@dataclass(slots=True)
class TionClimateExtraData(ExtraStoredData):
    """What the climate entity keeps across restarts and reloads."""

    pid_active: bool
    preset_mode: str | None
    preset_baseline: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        """Return the data to store."""
        return {
            "pid_active": self.pid_active,
            "preset_mode": self.preset_mode,
            "preset_baseline": self.preset_baseline,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild stored data, dropping values of another shape."""
        preset_mode = data.get("preset_mode")
        preset_baseline = data.get("preset_baseline")
        return cls(
            pid_active=data.get("pid_active") is True,
            preset_mode=preset_mode if isinstance(preset_mode, str) else None,
            preset_baseline=preset_baseline
            if isinstance(preset_baseline, dict)
            else None,
        )


class TionClimate(TionEntity[Breezer], ClimateEntity, RestoreEntity):
    """A breezer: power, fan speed and regime, presets, heating and the flap."""

    _attr_name = None
    _attr_translation_key = "tion_breezer"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: TionCoordinator, breezer: Breezer) -> None:
        """Create the climate entity of one breezer."""
        super().__init__(coordinator, breezer, None)
        presets = coordinator.config_entry.options.get(CONF_PRESETS, {})
        self._presets = TionPresetController(
            visible_presets(
                presets.get(breezer.id, {}),
                pid_configured=coordinator.pid.is_configured(breezer.id),
            )
        )
        # While the entity changes the regime, its passing states are no news.
        self._in_transition = False

    @property
    def _pid(self) -> TionPidManager:
        return self.coordinator.pid

    async def async_added_to_hass(self) -> None:
        """Follow the breezer's PID and restore the regime and preset."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._pid.add_listener(self.device_view.id, self._handle_pid_update)
        )
        if (last := await self.async_get_last_extra_data()) is not None:
            self._restore(TionClimateExtraData.from_dict(last.as_dict()))

    @property
    def extra_restore_state_data(self) -> TionClimateExtraData:
        """Return the PID flag, the active preset and the baseline to restore."""
        saved = self._presets.saved
        return TionClimateExtraData(
            pid_active=self._pid.is_active(self.device_view.id),
            preset_mode=self._presets.preset_mode,
            preset_baseline=baseline_to_storage(saved) if saved is not None else None,
        )

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
        if self._presets.has_presets:
            features |= ClimateEntityFeature.PRESET_MODE
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
        """Return auto (the room has auto mode), local PID (set up) and speeds."""
        modes = []
        if self.room_auto is not None:
            modes.append(FAN_AUTO)
        if self._pid.is_configured(self.device_view.id):
            modes.append(FAN_LOCAL_PID)
        speeds = range(1, (self.device_view.speed_max or 0) + 1)
        return [*modes, *(str(speed) for speed in speeds)]

    @property
    def fan_mode(self) -> str | None:
        """Return auto while the room's auto mode is on, local PID, or the speed."""
        if self._room_auto_enabled:
            return FAN_AUTO
        if self._pid.is_active(self.device_view.id):
            return FAN_LOCAL_PID
        breezer = self.device_view
        if breezer.is_on and breezer.speed is not None and breezer.speed >= 1:
            return str(breezer.speed)
        return None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return none and the breezer's presets."""
        return self._presets.preset_modes if self._presets.has_presets else None

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset, or none."""
        return self._presets.preset_mode if self._presets.has_presets else None

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
        """Return the room's mode, the speed (0 when off), heater power and PID."""
        breezer = self.device_view
        auto = self.room_auto
        attributes: dict[str, Any] = {
            "mode": None if auto is None else ("auto" if auto.enabled else "manual"),
            "speed": breezer.speed if breezer.is_on else 0,
        }
        if breezer.heater_power is not None:
            attributes["power"] = breezer.heater_power
        if self._pid.is_configured(breezer.id):
            attributes["pid_active"] = self._pid.is_active(breezer.id)
            attributes["pid_status"] = self._pid.status(breezer.id).value
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
        """Hand the speed to the room's auto mode or local PID, or set it."""
        breezer_id = self.device_view.id
        with self._transition():
            if fan_mode == FAN_LOCAL_PID:
                self._release_preset()
                await self._async_leave_auto()
                self._pid.start(breezer_id)
                return
            # Stopped first: releasing a PID preset would step PID once more.
            self._pid.stop(breezer_id)
            self._release_preset()
            if fan_mode == FAN_AUTO:
                await self.async_set_room_auto(enabled=True)
            else:
                await self._async_leave_auto()
                await self.async_send_command(speed=int(fan_mode))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Run the breezer as a preset says, or return to the regime before it."""
        if preset_mode == PRESET_NONE:
            await self._async_leave_preset()
        else:
            await self._async_enter_preset(preset_mode)
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set where the breezer takes air from."""
        await self.async_send_command(flap=FLAPS[SwingMode(swing_mode)])

    async def _async_power_off(self) -> None:
        with self._transition():
            self._pid.stop(self.device_view.id)
            self._release_preset()
            # The room's auto mode would turn the breezer back on.
            await self._async_leave_auto()
            await self.async_send_command(is_on=False)

    async def _async_leave_auto(self) -> None:
        # Before a manual change, or the auto mode overrides it.
        if self._room_auto_enabled:
            await self.async_set_room_auto(enabled=False)

    @property
    def _room_auto_enabled(self) -> bool:
        return (auto := self.room_auto) is not None and auto.enabled

    async def _async_enter_preset(self, name: str) -> None:
        preset = self._presets.preset(name)
        assert preset is not None  # HA checks the name against preset_modes
        breezer_id = self.device_view.id
        baseline = self._presets.saved or self._current_baseline()
        with self._transition():
            if isinstance(preset, ManualPreset):
                self._pid.stop(breezer_id)
                self._pid.set_preset_limits(breezer_id, None)
                await self._async_leave_auto()
                await self.async_send_command(is_on=True, speed=preset.speed)
            else:
                self._pid.set_preset_limits(
                    breezer_id, (preset.min_speed, preset.max_speed)
                )
                await self._async_leave_auto()
                self._pid.start(breezer_id)
        self._presets.activate(name, baseline)

    async def _async_leave_preset(self) -> None:
        if (baseline := self._presets.saved) is None:
            return
        breezer_id = self.device_view.id
        with self._transition():
            if isinstance(baseline, PidBaseline):
                self._pid.set_preset_limits(breezer_id, None)
                await self._async_leave_auto()
                self._pid.start(breezer_id)
            else:
                self._pid.stop(breezer_id)
                self._pid.set_preset_limits(breezer_id, None)
                await self._async_leave_auto()
                changes: dict[str, Any] = {"is_on": baseline.is_on}
                if baseline.speed is not None:
                    changes["speed"] = baseline.speed
                await self.async_send_command(**changes)
        self._presets.deactivate()

    def _current_baseline(self) -> Baseline:
        if self._pid.is_active(self.device_view.id):
            return PidBaseline()
        breezer = self.device_view
        return SpeedBaseline(breezer.speed, breezer.is_on is True)

    @contextmanager
    def _transition(self) -> Iterator[None]:
        self._in_transition = True
        try:
            yield
        finally:
            self._in_transition = False

    @callback
    def _handle_coordinator_update(self) -> None:
        self._release_diverged_preset()
        super()._handle_coordinator_update()

    @callback
    def _handle_pid_update(self) -> None:
        self._release_diverged_preset()
        self.async_write_ha_state()

    @callback
    def _release_diverged_preset(self) -> None:
        # A regime change that bypassed the preset ends it, without restoring.
        if self._in_transition or (preset := self._presets.active_preset()) is None:
            return
        if not self._runs(preset):
            self._release_preset()

    def _runs(self, preset: Preset) -> bool:
        breezer = self.device_view
        pid_active = self._pid.is_active(breezer.id)
        if isinstance(preset, PidPreset):
            return pid_active
        return (
            breezer.is_on is True
            and breezer.speed == preset.speed
            and not self._room_auto_enabled
            and not pid_active
        )

    @callback
    def _release_preset(self) -> None:
        self._presets.deactivate()
        self._pid.set_preset_limits(self.device_view.id, None)

    @callback
    def _restore(self, data: TionClimateExtraData) -> None:
        breezer_id = self.device_view.id
        name = data.preset_mode or PRESET_NONE
        preset = self._presets.preset(name)
        baseline = baseline_from_storage(data.preset_baseline)
        if preset is not None and baseline is not None:
            self._presets.restore(name, baseline)
            if isinstance(preset, PidPreset):
                self._pid.set_preset_limits(
                    breezer_id, (preset.min_speed, preset.max_speed)
                )
        # The room's cloud auto mode, turned on meanwhile, wins over PID.
        if (
            data.pid_active
            and self._pid.is_configured(breezer_id)
            and not self._room_auto_enabled
        ):
            self._pid.start(breezer_id)
        self._release_diverged_preset()
