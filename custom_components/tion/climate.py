"""Support for Tion breezer."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, Self

from homeassistant.components.climate import (
    FAN_AUTO,
    PRESET_NONE,
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
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import TionZoneDevice
from .const import (
    BREEZER_TYPES,
    CONF_PRESETS,
    DOMAIN,
    Heater,
    SwingMode,
    TionDeviceType,
    ZoneMode,
)
from .coordinator import TionDataUpdateCoordinator
from .presets import Preset, PresetBaseline, TionPresetController

_LOGGER = logging.getLogger(__name__)


@dataclass
class TionRestoreData(ExtraStoredData):
    """Climate state restored across restarts and reloads.

    Persisted through ``extra_restore_state_data``, which Home Assistant captures
    even while the entity is unavailable -- unlike state attributes, which are
    dropped for an unavailable entity. This keeps local PID and the active preset
    across a reload that lands while the breezer's gateway is offline.
    """

    pid_active: bool
    preset_mode: str | None
    preset_saved: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        """Return the payload to persist."""
        return {
            "pid_active": self.pid_active,
            "preset_mode": self.preset_mode,
            "preset_saved": self.preset_saved,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild restore data from a persisted payload."""
        return cls(
            pid_active=bool(data.get("pid_active")),
            preset_mode=data.get("preset_mode"),
            preset_saved=data.get("preset_saved"),
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up climate Tion entities."""
    coordinator: TionDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    devices = coordinator.get_devices()
    entities = [
        TionClimate(coordinator, device)
        for device in devices
        if device.guid and device.type in BREEZER_TYPES
    ]

    async_add_entities(entities)
    return True


class TionClimate(
    CoordinatorEntity[TionDataUpdateCoordinator], RestoreEntity, ClimateEntity
):
    """Tion climate devices,include air conditioner,heater."""

    _attr_translation_key = "tion_breezer"

    def __init__(
        self, coordinator: TionDataUpdateCoordinator, breezer: TionZoneDevice
    ) -> None:
        """Initialize climate device for Tion Breezer."""
        super().__init__(coordinator)

        self._breezer_guid = breezer.guid
        self._attr_name = breezer.name
        self._type = breezer.type
        self._attr_max_temp = breezer.t_max
        self._attr_min_temp = breezer.t_min
        self._breezer_valid = breezer.valid
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
        self._presets = TionPresetController(
            self.coordinator.config_entry.options.get(CONF_PRESETS, {}).get(
                breezer.guid, {}
            )
        )
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
        self._swing_modes = []

        self._manual_fan_modes = [
            str(speed) for speed in range(1, breezer.max_speed + 1)
        ]

        self._attr_supported_features = ClimateEntityFeature.FAN_MODE
        if self._gate is not None:
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
            self._swing_modes.append(SwingMode.SWING_OUTSIDE)

        if breezer.data.heater_installed or breezer.data.heater_type is not None:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._hvac_modes.append(HVACMode.HEAT)

        if (MAJOR_VERSION, MINOR_VERSION) >= (2024, 2):
            self._enable_turn_on_off_backwards_compatibility = False
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON

        if self._presets.has_presets:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        A breezer reaches the cloud only through its MagicAir gateway, so it is
        available only while that gateway is online; its own ``is_online`` flag
        freezes stale once the gateway drops.
        """
        return bool(
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.is_breezer_reachable(self._breezer_guid)
            and self._breezer_valid
            and self._zone_valid
        )

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
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Provides extra attributes."""
        attrs = {
            "mode": self.mode,
            "speed": self.speed,
        }
        if self._heater_power is not None:
            attrs.update({"power": self._heater_power})

        attrs.update(
            self.coordinator.pid_manager.extra_state_attributes(self._breezer_guid)
        )

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
        return self._attr_min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._attr_max_temp

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._t_out if self._breezer_valid else None

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._t_set if self._breezer_valid else None

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

        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation if supported."""
        hvac_mode = self.hvac_mode
        if hvac_mode is None:
            return None

        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if self._heater_power or self._heater_enabled:
            return HVACAction.HEATING

        return HVACAction.FAN

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes."""
        if self._fan_auto_available():
            return [FAN_AUTO, *self._manual_fan_modes]

        return list(self._manual_fan_modes)

    def _fan_auto_available(self) -> bool:
        """Return if Fan Auto can be selected for this breezer."""
        pid_manager = self.coordinator.pid_manager
        if pid_manager.is_configured(self._breezer_guid):
            return True

        zone = self.coordinator.get_device_zone(self._breezer_guid)
        if zone is None:
            return True

        return not any(
            device.guid
            and device.type in BREEZER_TYPES
            and pid_manager.is_configured(device.guid)
            for device in zone.devices
        )

    @property
    def fan_mode(self) -> str | None:
        """Return the fan setting."""
        if self.coordinator.pid_manager.is_active(self._breezer_guid):
            return FAN_AUTO

        if self._mode == FAN_AUTO:
            return FAN_AUTO

        return str(self.speed) if self.speed is not None else None

    def _exact_fan_mode(self) -> str:
        """Return a log-friendly label for the current fan regime.

        Distinguishes the two auto sources (``local_pid`` vs cloud ``auto``)
        that ``fan_mode`` collapses into ``FAN_AUTO``, so logs make clear which
        controller actually drives the breezer.
        """
        if self.coordinator.pid_manager.is_active(self._breezer_guid):
            return "local_pid"
        if self._mode == ZoneMode.AUTO:
            return "cloud_auto"
        return f"manual(speed={self.speed})"

    @property
    def swing_modes(self) -> list[SwingMode]:
        """Return the list of available preset modes."""
        return self._swing_modes

    @property
    def swing_mode(self) -> SwingMode | None:
        """Return current swing mode."""
        if self._type == TionDeviceType.BREEZER_4S:
            match self._gate:
                case 0:
                    return SwingMode.SWING_OUTSIDE
                case 1:
                    return SwingMode.SWING_INSIDE
        elif self._type == TionDeviceType.BREEZER_3S:
            match self._gate:
                case 0:
                    return SwingMode.SWING_INSIDE
                case 1:
                    return SwingMode.SWING_MIXED
                case 2:
                    return SwingMode.SWING_OUTSIDE

        return None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the list of available preset modes."""
        return self._presets.preset_modes if self._presets.has_presets else None

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        return self._presets.preset_mode if self._presets.has_presets else None

    @property
    def mode(self) -> ZoneMode | None:
        """Return the current mode."""
        return self._mode if self._zone_valid else None

    @property
    def speed(self) -> int | None:
        """Return the current speed."""
        try:
            return int(self._speed)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert breezer speed value to int: %s. Error: %s",
                self.name,
                self._speed,
                e,
            )

        return None

    @property
    def speed_min_set(self) -> int | None:
        """Return the breezer's lower auto-speed limit."""
        return self._speed_min_set

    @property
    def speed_max_set(self) -> int | None:
        """Return the breezer's upper auto-speed limit."""
        return self._speed_max_set

    @property
    def heater_enabled(self) -> bool:
        """Return if heater active now."""
        if self._type == TionDeviceType.BREEZER_4S:
            return self._heater_mode == Heater.ON

        return self._heater_enabled or False

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        self._load_zone()
        self._load_breezer()
        self._set_swing_modes()
        await super().async_added_to_hass()
        if (restored := await self.async_get_last_extra_data()) is not None:
            data = TionRestoreData.from_dict(restored.as_dict())
            self._restore_local_pid(data.pid_active)
            self._restore_preset(data.preset_mode, data.preset_saved)

    @property
    def extra_restore_state_data(self) -> TionRestoreData:
        """Return state to restore that must survive entity unavailability."""
        saved = self._presets.saved
        return TionRestoreData(
            pid_active=self.coordinator.pid_manager.is_active(self._breezer_guid),
            preset_mode=self.preset_mode,
            preset_saved=saved.to_storage() if saved else None,
        )

    @callback
    def _restore_local_pid(self, pid_active: bool) -> None:
        """Restore local PID active state after Home Assistant restart."""
        pid_manager = self.coordinator.pid_manager
        if not pid_manager.is_configured(self._breezer_guid):
            return
        if pid_active:
            _LOGGER.debug("%s: restoring active local PID", self.name)
            pid_manager.start_breezer_pid(self._breezer_guid)
        else:
            _LOGGER.debug(
                "%s: no need to restore local PID (restored pid_active=%s)",
                self.name,
                pid_active,
            )

    @callback
    def _restore_preset(
        self, preset_mode: str | None, preset_saved: dict[str, Any] | None
    ) -> None:
        """Restore active preset and saved preset after Home Assistant restart."""
        if not self._presets.has_presets:
            return
        if preset_mode is None or preset_mode == PRESET_NONE:
            _LOGGER.debug(
                "%s: no preset to restore (restored preset_mode=%s)",
                self.name,
                preset_mode,
            )
            return
        saved = PresetBaseline.from_storage(preset_saved)
        _LOGGER.debug(
            "%s: restoring preset '%s' with saved baseline %s",
            self.name,
            preset_mode,
            saved,
        )
        self._presets.restore(preset_mode, saved)
        # Re-derive the preset's desired fields from its definition (they are
        # not persisted) so the reconciler holds them and the preset is not
        # released on the first coordinator update after restart.
        if (preset := self._presets.active_preset()) is not None:
            self._write_preset_desired(preset)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._load_zone()
        self._load_breezer()
        if self._presets.preset_mode != PRESET_NONE:
            preset = self._presets.active_preset()
            if preset is not None and not self.coordinator.reconciler.holds(
                self._breezer_guid, preset.desired_fields()
            ):
                _LOGGER.info(
                    "%s: preset '%s' released (managed field changed)",
                    self.name,
                    self._presets.preset_mode,
                )
                self._presets.deactivate()
        _LOGGER.debug(
            "%s: state: preset=%s, fan_mode=%s, speed=%s, min=%s, max=%s",
            self.name,
            self.preset_mode,
            self._exact_fan_mode(),
            self.speed,
            self._speed_min_set,
            self._speed_max_set,
        )
        super()._handle_coordinator_update()

    async def async_turn_on(self) -> None:
        """Turn breezer on."""
        await self.async_set_hvac_mode(
            HVACMode.HEAT if self.heater_enabled else HVACMode.FAN_ONLY
        )

    async def async_turn_off(self) -> None:
        """Turn breezer off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        """Set new target operation mode by writing the breezer/zone desired state."""
        if hvac_mode not in self._hvac_modes:
            _LOGGER.warning("%s: unsupported hvac mode '%s'", self.name, hvac_mode)
            return

        if hvac_mode == self.hvac_mode:
            _LOGGER.debug(
                "%s: no need to change HVAC mode: %s already set", self.name, hvac_mode
            )
            return

        _LOGGER.debug(
            "%s: changing HVAC mode (%s -> %s)", self.name, self.hvac_mode, hvac_mode
        )
        # Power on/off conflicts with an active preset, so leave it first.
        self._release_active_preset()
        reconciler = self.coordinator.reconciler
        if hvac_mode == HVACMode.OFF:
            self.coordinator.pid_manager.stop_breezer_pid(self._breezer_guid)
            reconciler.set_breezer(self._breezer_guid, {"is_on": False})
            if self._zone_guid is not None:
                reconciler.set_zone(self._zone_guid, {"mode": ZoneMode.MANUAL})
        else:
            fields = {"is_on": True, **self._heater_fields(hvac_mode == HVACMode.HEAT)}
            reconciler.set_breezer(self._breezer_guid, fields)
        await self._push()

    def _heater_fields(self, enabled: bool) -> dict[str, Any]:
        """Return the breezer desired field that enables/disables the heater."""
        if self._type == TionDeviceType.BREEZER_4S:
            return {"heater_mode": Heater.ON if enabled else Heater.OFF}
        return {"heater_enabled": enabled}

    def _release_active_preset(self) -> None:
        """Drop preset-managed desired fields and clear the preset, if any.

        A manual command that overlaps a preset's managed fields (fan speed/auto,
        power on/off) means the user left the preset by hand; releasing the fields
        keeps ``set_breezer`` from layering the command on top of stale preset
        overrides.
        """
        if self._presets.preset_mode == PRESET_NONE:
            return
        _LOGGER.info(
            "%s: releasing preset '%s' (manual command overlaps managed fields)",
            self.name,
            self._presets.preset_mode,
        )
        self.coordinator.reconciler.release(
            self._breezer_guid, self._presets.managed_fields
        )
        self._presets.deactivate()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature by writing the breezer desired state."""
        if not self._attr_supported_features & ClimateEntityFeature.TARGET_TEMPERATURE:
            _LOGGER.warning("%s: service not supported", self.name)
            return

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            _LOGGER.warning("%s: undefined target temperature", self.name)
            return

        if temperature == self.target_temperature:
            _LOGGER.debug(
                "%s: no need to change target temperature: %s already set",
                self.name,
                temperature,
            )
            return

        self.coordinator.reconciler.set_breezer(
            self._breezer_guid, {"t_set": int(temperature)}
        )
        await self._push()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode (cloud/PID auto or a manual speed)."""
        if fan_mode not in self.fan_modes:
            _LOGGER.warning("%s: unsupported fan mode '%s'", self.name, fan_mode)
            return

        if fan_mode == FAN_AUTO:
            self._release_active_preset()
            self._enter_auto_desired()
            await self._push()
            return

        try:
            new_speed = int(fan_mode)
        except (TypeError, ValueError) as e:
            _LOGGER.warning(
                "%s: unable to convert new fan mode to int: %s. Error: %s",
                self.name,
                fan_mode,
                e,
            )
            return

        if (
            self._presets.preset_mode == PRESET_NONE
            and self.fan_mode != FAN_AUTO
            and self.speed == new_speed
            and self._mode == ZoneMode.MANUAL
        ):
            _LOGGER.debug("%s: no need to change fan mode: %s set", self.name, fan_mode)
            return

        _LOGGER.debug("%s: changing breezer speed to %s (manual)", self.name, new_speed)
        # A manual speed conflicts with an active preset and with local PID/auto.
        self._release_active_preset()
        self.coordinator.pid_manager.stop_breezer_pid(self._breezer_guid)
        if self._zone_guid is not None:
            self.coordinator.reconciler.set_zone(
                self._zone_guid, {"mode": ZoneMode.MANUAL}
            )
        self.coordinator.reconciler.set_breezer(
            self._breezer_guid, {"speed": new_speed}
        )
        await self._push()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set a preset by writing its desired state (synchronous, no rollback)."""
        if preset_mode not in self._presets.preset_modes:
            _LOGGER.warning("%s: unsupported preset mode '%s'", self.name, preset_mode)
            return

        if preset_mode == self._presets.preset_mode:
            _LOGGER.debug(
                "%s: no need to change preset: %s already set", self.name, preset_mode
            )
            return

        target = self._presets.preset(preset_mode)
        if target is not None and target.is_auto() and FAN_AUTO not in self.fan_modes:
            _LOGGER.warning(
                "%s: cannot apply auto preset '%s', Fan Auto is unavailable",
                self.name,
                preset_mode,
            )
            return

        # The baseline comes from the desired overlay (not a live snapshot) and the
        # desired write is synchronous: there is no await before it, so a
        # restart-mode cancellation cannot interleave and pollute the baseline.
        if preset_mode == PRESET_NONE:
            _LOGGER.debug(
                "%s: leaving preset '%s', restoring baseline %s",
                self.name,
                self._presets.preset_mode,
                self._presets.saved,
            )
            self._restore_preset_baseline(self._presets.saved)
            self._presets.deactivate()
        else:
            baseline = PresetBaseline(
                overrides=self._managed_overrides(),
                was_auto=self.fan_mode == FAN_AUTO,
            )
            _LOGGER.debug(
                "%s: entering preset '%s' (from fan_mode=%s), saving baseline %s",
                self.name,
                preset_mode,
                self._exact_fan_mode(),
                baseline,
            )
            self._presets.activate(preset_mode, baseline)
            self._write_preset_desired(target)

        await self._push()
        _LOGGER.debug(
            "%s: applied preset '%s': fan_mode=%s, speed=%s, min=%s, max=%s",
            self.name,
            preset_mode,
            self._exact_fan_mode(),
            self.speed,
            self._speed_min_set,
            self._speed_max_set,
        )

    def _managed_overrides(self) -> dict[str, Any]:
        """Return the current desired overlay restricted to preset-managed fields."""
        current = self.coordinator.reconciler.current_breezer(self._breezer_guid)
        return {
            field: current[field]
            for field in self._presets.managed_fields
            if field in current
        }

    def _write_preset_desired(self, preset: Preset) -> None:
        """Write a preset's desired fields and arm/disarm its mode."""
        self.coordinator.reconciler.set_breezer(
            self._breezer_guid, preset.desired_fields()
        )
        if preset.is_auto():
            self._enter_auto_desired()
        else:
            self.coordinator.pid_manager.stop_breezer_pid(self._breezer_guid)
            if self._zone_guid is not None:
                self.coordinator.reconciler.set_zone(
                    self._zone_guid, {"mode": ZoneMode.MANUAL}
                )

    def _restore_preset_baseline(self, baseline: PresetBaseline | None) -> None:
        """Restore the desired overlay and mode saved before a preset."""
        if baseline is None:
            return
        if baseline.overrides:
            self.coordinator.reconciler.set_breezer(
                self._breezer_guid, baseline.overrides
            )
        else:
            self.coordinator.reconciler.release(
                self._breezer_guid, self._presets.managed_fields
            )
        if baseline.was_auto:
            self._enter_auto_desired()
        else:
            self.coordinator.pid_manager.stop_breezer_pid(self._breezer_guid)
            if self._zone_guid is not None:
                self.coordinator.reconciler.set_zone(
                    self._zone_guid, {"mode": ZoneMode.MANUAL}
                )

    def _enter_auto_desired(self) -> None:
        """Write desired auto mode (local PID if configured, else cloud auto)."""
        if self.coordinator.pid_manager.is_configured(self._breezer_guid):
            self.coordinator.pid_manager.start_breezer_pid(self._breezer_guid)
        elif self._zone_guid is not None:
            self.coordinator.reconciler.set_zone(
                self._zone_guid, {"mode": ZoneMode.AUTO}
            )

    async def _push(self) -> None:
        """Reconcile desired state now (optimistic + dispatch), then refresh.

        The immediate reconcile applies the breezer payload to the coordinator's
        data optimistically; reloading the entity from it reflects the change in
        the UI before the trailing refresh lands the authoritative cloud state.
        """
        if self.coordinator.data is not None:
            self.coordinator.reconciler.reconcile(self.coordinator.data)
        self._load_breezer()
        self._load_zone()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set Tion breezer air gate by writing the breezer desired state."""
        if swing_mode not in self._swing_modes:
            _LOGGER.debug("%s: not supported swing mode %s", self.name, swing_mode)
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
            _LOGGER.debug(
                "%s: changing gate (%s -> %s)", self.name, self.swing_mode, swing_mode
            )
            self.coordinator.reconciler.set_breezer(
                self._breezer_guid, {"gate": new_gate}
            )
            await self._push()

    def _set_swing_modes(self):
        if self._gate is None:
            self._swing_modes = []
            return

        self._swing_modes = [SwingMode.SWING_OUTSIDE]

        if self._mode == ZoneMode.MANUAL:
            self._swing_modes.append(SwingMode.SWING_INSIDE)
            if self._type == TionDeviceType.BREEZER_3S:
                self._swing_modes.append(SwingMode.SWING_MIXED)

    def _load_breezer(self):
        """Update breezer data from API."""
        if device_data := self.coordinator.get_device(self._breezer_guid):
            self._attr_name = device_data.name
            self._breezer_guid = device_data.guid
            self._breezer_valid = device_data.valid
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

        return self.available

    def _load_zone(self) -> bool:
        """Update zone data from API."""
        if zone_data := self.coordinator.get_device_zone(self._breezer_guid):
            old_mode = self._mode
            self._mode = zone_data.mode.current
            self._zone_guid = zone_data.guid
            self._zone_name = zone_data.name
            self._zone_valid = zone_data.valid

            if old_mode != self._mode:
                self._set_swing_modes()

            try:
                self._target_co2 = int(zone_data.mode.auto_set.co2)
            except (TypeError, ValueError) as e:
                _LOGGER.warning(
                    "%s: unable to convert target CO2 value to int: %s. Error: %s",
                    self.name,
                    zone_data.mode.auto_set.co2,
                    e,
                )

        return self.available
