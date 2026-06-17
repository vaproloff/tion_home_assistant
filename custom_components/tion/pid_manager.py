"""Runtime manager for local Tion CO2 PID control."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .client import TionError
from .const import (
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    DEFAULT_PID_BASE_OUTPUT,
    DEFAULT_PID_KD,
    DEFAULT_PID_KI,
    DEFAULT_PID_KP,
    DEFAULT_TARGET_CO2,
    PID_STATUS_INACTIVE,
    PID_STATUS_NOT_CONFIGURED,
    PID_STATUS_PAUSED_DEVICE_UNAVAILABLE,
    PID_STATUS_PAUSED_INVALID_DEVICE_DATA,
    PID_STATUS_PAUSED_SENSOR_UNAVAILABLE,
    PID_STATUS_RUNNING,
    PID_STATUS_SEND_FAILED,
    ZoneMode,
)
from .coordinator import TionData, TionDataUpdateCoordinator
from .pid import PidCoefficients, PidController, PidOutput

_LOGGER = logging.getLogger(__name__)


def _int_or_default(value: Any, default: int | None) -> int | None:
    """Convert an API value to int or return a default."""
    try:
        return int(value)
    except TypeError, ValueError:
        return default


class _TionBreezerPidController:
    """Manage local PID runtime for one breezer."""

    def __init__(self, manager: TionPidManager, breezer_guid: str) -> None:
        """Initialize a breezer PID controller."""
        self.manager = manager
        self.hass = manager.hass
        self.entry = manager.entry
        self.coordinator = manager.coordinator
        self.breezer_guid = breezer_guid

        self.active = False
        self.source_co2: float | None = None
        self.error: float | None = None
        self.output_speed: int | None = None
        self.status = PID_STATUS_INACTIVE
        self.last_update: str | None = None
        self.target_co2 = DEFAULT_TARGET_CO2

        options = manager.entry.options.get(CONF_PID_BREEZERS, {}).get(breezer_guid, {})
        self.controller = PidController(
            PidCoefficients(
                kp=float(options.get(CONF_PID_KP, DEFAULT_PID_KP)),
                ki=float(options.get(CONF_PID_KI, DEFAULT_PID_KI)),
                kd=float(options.get(CONF_PID_KD, DEFAULT_PID_KD)),
                base_output=float(
                    options.get(CONF_PID_BASE_OUTPUT, DEFAULT_PID_BASE_OUTPUT)
                ),
            )
        )

    def start(self) -> None:
        """Arm this breezer PID controller."""
        if not self.active:
            _LOGGER.debug("Arming local PID for breezer %s", self.breezer_guid)

        self.active = True
        self._set_status(PID_STATUS_RUNNING)

    def stop(self, status: str = PID_STATUS_INACTIVE) -> None:
        """Disarm this breezer PID controller."""
        if self.active:
            _LOGGER.debug("Disarming local PID for breezer %s", self.breezer_guid)

        self.active = False
        self.controller.reset()
        self._set_status(status)

    def set_target_co2(self, target_co2: float) -> None:
        """Set the local target CO2 for this breezer."""
        _LOGGER.debug(
            "Changing local PID target CO2 for breezer %s to %s",
            self.breezer_guid,
            target_co2,
        )
        self.target_co2 = target_co2
        self.controller.reset()

    def extra_state_attributes(self, source_entity_id: str | None) -> dict[str, Any]:
        """Return PID attributes for a climate entity."""
        return {
            "pid_active": self.active,
            "pid_source_entity_id": source_entity_id,
            "pid_source_co2": self.source_co2,
            "pid_error": self.error,
            "pid_output_speed": self.output_speed,
            "pid_status": self.status,
            "pid_last_update": self.last_update,
        }

    async def async_evaluate(self, data: TionData) -> PidOutput | None:
        """Evaluate this PID controller and send a command if needed."""
        options = self.entry.options.get(CONF_PID_BREEZERS, {}).get(
            self.breezer_guid, {}
        )
        if not self.active:
            self._set_status(PID_STATUS_INACTIVE)
            return None

        if not self.manager.is_configured(self.breezer_guid):
            self.stop(PID_STATUS_NOT_CONFIGURED)
            return None

        zone = data.zone(self.breezer_guid)
        if zone is None or not zone.valid:
            self._pause(PID_STATUS_PAUSED_DEVICE_UNAVAILABLE)
            return None

        if zone.mode.current == ZoneMode.AUTO:
            target_co2 = _int_or_default(zone.mode.auto_set.co2, int(self.target_co2))
            if target_co2 is None:
                target_co2 = DEFAULT_TARGET_CO2

            _LOGGER.debug(
                "Returning Tion zone %s to manual mode for local PID control",
                zone.name,
            )
            try:
                await self.coordinator.async_send_zone(
                    guid=zone.guid,
                    mode=ZoneMode.MANUAL,
                    co2=target_co2,
                    request_refresh=False,
                    track_stale=False,
                )
            except TionError as err:
                _LOGGER.warning(
                    "Unable to disable MagicAir auto mode for local PID control: %s",
                    err,
                )
                self._pause(PID_STATUS_SEND_FAILED)
                return None

        source_entity_id = options[CONF_CO2_SENSOR_ENTITY_ID]
        co2_state = self.hass.states.get(source_entity_id)
        if co2_state is None or co2_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self.source_co2 = None
            self._pause(PID_STATUS_PAUSED_SENSOR_UNAVAILABLE)
            return None

        try:
            source_co2 = float(co2_state.state)
        except TypeError, ValueError:
            self.source_co2 = None
            self._pause(PID_STATUS_PAUSED_SENSOR_UNAVAILABLE)
            return None

        device = data.device(self.breezer_guid)
        self.source_co2 = source_co2
        if device is None or not device.valid or not device.is_online:
            self._pause(PID_STATUS_PAUSED_DEVICE_UNAVAILABLE)
            return None

        device_max_speed = _int_or_default(device.max_speed, 0)
        speed_min = _int_or_default(device.data.speed_min_set, 0)
        speed_max = _int_or_default(device.data.speed_max_set, device_max_speed)
        t_set = _int_or_default(device.data.t_set, None)
        if (
            device_max_speed is None
            or device_max_speed <= 0
            or speed_min is None
            or speed_max is None
            or t_set is None
        ):
            self._pause(PID_STATUS_PAUSED_INVALID_DEVICE_DATA)
            return None

        output = self.controller.calculate(
            source_co2=source_co2,
            target_co2=self.target_co2,
            speed_min=speed_min,
            speed_max=speed_max,
            device_max_speed=device_max_speed,
            now=self.hass.loop.time(),
        )

        self.error = output.error
        self.output_speed = output.speed
        self._set_status(PID_STATUS_RUNNING)

        try:
            current_speed = int(device.data.speed)
        except TypeError, ValueError:
            command_changed = True
        else:
            command_changed = (
                current_speed != output.speed or bool(device.data.is_on) != output.is_on
            )

        if not command_changed:
            _LOGGER.debug(
                "Skipping unchanged local PID command for %s: is_on=%s speed=%s",
                device.name,
                output.is_on,
                output.speed,
            )
            return output

        _LOGGER.debug(
            "Sending local PID command for %s: co2=%s target=%s error=%s is_on=%s "
            "speed=%s",
            device.name,
            source_co2,
            self.target_co2,
            output.error,
            output.is_on,
            output.speed,
        )
        try:
            await self.coordinator.async_send_breezer(
                guid=device.guid,
                is_on=output.is_on,
                t_set=t_set,
                speed=output.speed,
                speed_min_set=speed_min,
                speed_max_set=speed_max,
                heater_enabled=device.data.heater_enabled,
                heater_mode=device.data.heater_mode,
                gate=device.data.gate,
                request_refresh=False,
                track_stale=False,
            )
        except TionError as err:
            _LOGGER.warning(
                "Unable to send local PID command for %s: %s",
                device.name,
                err,
            )
            self._pause(PID_STATUS_SEND_FAILED)
            return None

        # Optimistically reflect the sent command so the UI updates immediately
        # and the next cycle's command_changed check compares against it.
        device.data.speed = output.speed
        device.data.is_on = output.is_on

        return output

    def _pause(self, status: str) -> None:
        """Pause updates without disarming PID."""
        self.error = None
        self.output_speed = None
        self._set_status(status)

    def _set_status(self, status: str) -> None:
        """Update runtime status timestamp."""
        if self.status != status:
            _LOGGER.debug(
                "Local PID status for breezer %s changed: %s -> %s",
                self.breezer_guid,
                self.status,
                status,
            )
        self.status = status
        self.last_update = dt_util.utcnow().isoformat()


class TionPidManager:
    """Manage local PID control for all breezers in one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: TionDataUpdateCoordinator,
    ) -> None:
        """Initialize the PID manager."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._controllers: dict[str, _TionBreezerPidController] = {}

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Return an unload callback.

        Local PID is evaluated inside the coordinator update cycle, so there is
        no separate timer to start here.
        """
        return self.async_stop

    @callback
    def async_stop(self) -> None:
        """Disarm all local PID controllers."""
        for controller in self._controllers.values():
            controller.active = False

    def configured_breezers(self) -> set[str]:
        """Return breezers that have local PID control enabled."""
        return {
            breezer_guid
            for breezer_guid in self.entry.options.get(CONF_PID_BREEZERS, {})
            if self.is_configured(breezer_guid)
        }

    def has_enabled_pid(self) -> bool:
        """Return if at least one breezer has local PID control enabled."""
        return bool(self.configured_breezers())

    def has_active_pid(self) -> bool:
        """Return if at least one breezer has active local PID control."""
        return any(controller.active for controller in self._controllers.values())

    def is_configured(self, breezer_guid: str) -> bool:
        """Return if a breezer has enabled external CO2 PID settings."""
        options = self._pid_options(breezer_guid)
        return bool(
            options.get(CONF_PID_ENABLED) and options.get(CONF_CO2_SENSOR_ENTITY_ID)
        )

    def is_active(self, breezer_guid: str) -> bool:
        """Return if PID is currently armed for a breezer."""
        controller = self._controllers.get(breezer_guid)
        return bool(
            controller and controller.active and self.is_configured(breezer_guid)
        )

    @callback
    def start_breezer_pid(self, breezer_guid: str) -> bool:
        """Arm local PID control for a breezer."""
        controller = self._controller(breezer_guid)
        if controller is None:
            _LOGGER.debug(
                "Cannot arm local PID for breezer %s: PID is not configured",
                breezer_guid,
            )
            return False

        controller.start()
        # PID now runs inside the coordinator cycle; kick an immediate refresh so
        # arming takes effect without waiting for the next interval.
        self.hass.async_create_task(self.coordinator.async_request_refresh())
        return True

    @callback
    def stop_breezer_pid(self, breezer_guid: str) -> bool:
        """Disarm local PID control for a breezer."""
        controller = self._controllers.get(breezer_guid)
        if controller is None:
            return False

        controller.stop()
        return True

    def get_target_co2(self, breezer_guid: str) -> float:
        """Return the local target CO2 for a breezer."""
        controller = self._controller(breezer_guid)
        return controller.target_co2 if controller is not None else DEFAULT_TARGET_CO2

    @callback
    def set_target_co2(self, breezer_guid: str, target_co2: float) -> None:
        """Set the local target CO2 for a breezer."""
        controller = self._controller(breezer_guid)
        if controller is None:
            _LOGGER.debug(
                "Ignoring local PID target CO2 for breezer %s: PID is not configured",
                breezer_guid,
            )
            return
        controller.set_target_co2(target_co2)

    def extra_state_attributes(self, breezer_guid: str) -> dict[str, Any]:
        """Return PID attributes for a climate entity."""
        options = self._pid_options(breezer_guid)
        source_entity_id = options.get(CONF_CO2_SENSOR_ENTITY_ID)
        controller = self._controllers.get(breezer_guid)
        if controller is not None and self.is_configured(breezer_guid):
            return controller.extra_state_attributes(source_entity_id)

        return {
            "pid_active": False,
            "pid_source_entity_id": source_entity_id,
            "pid_source_co2": None,
            "pid_error": None,
            "pid_output_speed": None,
            "pid_status": PID_STATUS_INACTIVE,
            "pid_last_update": None,
        }

    async def async_evaluate_all(self, data: TionData) -> None:
        """Evaluate active PID controllers on freshly fetched coordinator data."""
        breezer_guids = {
            breezer_guid
            for breezer_guid, controller in self._controllers.items()
            if controller.active
        }
        for breezer_guid in breezer_guids:
            try:
                await self.async_evaluate_breezer(breezer_guid, data)
            except (
                Exception
            ):  # broad catch is intentional; isolate per-breezer failures
                _LOGGER.exception(
                    "Unexpected error evaluating local PID for breezer %s",
                    breezer_guid,
                )

    async def async_evaluate_breezer(
        self, breezer_guid: str, data: TionData
    ) -> PidOutput | None:
        """Evaluate one PID controller and send a command if needed."""
        controller = self._controllers.get(breezer_guid)
        if controller is None:
            controller = self._controller(breezer_guid)
        if controller is None:
            return None
        return await controller.async_evaluate(data)

    def _pid_options(self, breezer_guid: str) -> dict[str, Any]:
        """Return stored PID options for a breezer."""
        return self.entry.options.get(CONF_PID_BREEZERS, {}).get(breezer_guid, {})

    def _controller(self, breezer_guid: str) -> _TionBreezerPidController | None:
        """Return a configured PID controller for a breezer."""
        if not self.is_configured(breezer_guid):
            return None

        controller = self._controllers.get(breezer_guid)
        if controller is None:
            _LOGGER.debug("Creating local PID controller for breezer %s", breezer_guid)
            controller = _TionBreezerPidController(self, breezer_guid)
            self._controllers[breezer_guid] = controller
        return controller
