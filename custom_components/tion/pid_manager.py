"""Runtime manager for local Tion CO2 PID control."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .client import TionZoneDevice
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
    ZoneMode,
)
from .coordinator import TionData, TionDataUpdateCoordinator
from .pid import PidCoefficients, PidController

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
            _LOGGER.debug(
                "%s: arming local PID",
                self.manager.breezer_name(self.breezer_guid),
            )

        self.active = True
        self._set_status(PID_STATUS_RUNNING)

    def stop(self, status: str = PID_STATUS_INACTIVE) -> None:
        """Disarm this breezer PID controller."""
        if self.active:
            _LOGGER.debug(
                "%s: disarming local PID",
                self.manager.breezer_name(self.breezer_guid),
            )

        self.active = False
        self.controller.reset()
        self._set_status(status)

    def set_target_co2(self, target_co2: float) -> None:
        """Set the local target CO2 for this breezer."""
        _LOGGER.debug(
            "%s: changing local PID target CO2 to %s",
            self.manager.breezer_name(self.breezer_guid),
            target_co2,
        )
        self.target_co2 = target_co2
        self.controller.reset()

    def extra_state_attributes(self) -> dict[str, Any]:
        """Return PID attributes for a climate entity."""
        return {
            "pid_active": self.active,
            "pid_status": self.status,
        }

    def evaluate(self, data: TionData) -> None:
        """Write this breezer's desired state into the reconciler (no I/O)."""
        options = self.entry.options.get(CONF_PID_BREEZERS, {}).get(
            self.breezer_guid, {}
        )
        if not self.active:
            self._set_status(PID_STATUS_INACTIVE)
            return

        if not self.manager.is_configured(self.breezer_guid):
            self.stop(PID_STATUS_NOT_CONFIGURED)
            return

        zone = data.zone(self.breezer_guid)
        if zone is None or not zone.valid:
            self.pause(PID_STATUS_PAUSED_DEVICE_UNAVAILABLE)
            return

        zone_target_co2: int | None = None
        if zone.mode.current == ZoneMode.AUTO:
            zone_target_co2 = _int_or_default(
                zone.mode.auto_set.co2, int(self.target_co2)
            )
            if zone_target_co2 is None:
                zone_target_co2 = DEFAULT_TARGET_CO2

        source_entity_id = options[CONF_CO2_SENSOR_ENTITY_ID]
        co2_state = self.hass.states.get(source_entity_id)
        if co2_state is None or co2_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self.source_co2 = None
            self.pause(PID_STATUS_PAUSED_SENSOR_UNAVAILABLE)
            return

        try:
            source_co2 = float(co2_state.state)
        except TypeError, ValueError:
            self.source_co2 = None
            self.pause(PID_STATUS_PAUSED_SENSOR_UNAVAILABLE)
            return

        device = data.device(self.breezer_guid)
        self.source_co2 = source_co2
        if (
            device is None
            or not device.valid
            or not data.is_breezer_reachable(self.breezer_guid)
        ):
            self.pause(PID_STATUS_PAUSED_DEVICE_UNAVAILABLE)
            return

        # Honor just-changed auto limits immediately: the desired overlay holds
        # the user's new value before the cloud confirms it, so reading it here
        # avoids a one-cycle lag versus the stale reported limit.
        desired = self.coordinator.reconciler.current_breezer(self.breezer_guid)
        device_max_speed = _int_or_default(device.max_speed, 0)
        speed_min = _int_or_default(
            desired.get("speed_min_set", device.data.speed_min_set), 0
        )
        speed_max = _int_or_default(
            desired.get("speed_max_set", device.data.speed_max_set), device_max_speed
        )
        t_set = _int_or_default(device.data.t_set, None)
        if (
            device_max_speed is None
            or device_max_speed <= 0
            or speed_min is None
            or speed_max is None
            or t_set is None
        ):
            self.pause(PID_STATUS_PAUSED_INVALID_DEVICE_DATA)
            return

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

        breezer_name = self.manager.breezer_name(self.breezer_guid, device)
        _LOGGER.debug(
            "%s: PID calculation: source_co2=%s target_co2=%s error=%s "
            "p=%s i=%s d=%s raw_output=%s min_speed=%s max_speed=%s "
            "pid_output_speed=%s is_on=%s",
            breezer_name,
            source_co2,
            self.target_co2,
            output.error,
            output.p_output,
            output.i_output,
            output.d_output,
            output.raw_output,
            speed_min,
            speed_max,
            output.speed,
            output.is_on,
        )

        # Local PID owns is_on and speed; off implies speed 0. The reconciler
        # decides whether anything actually needs sending (diff) and dispatches.
        if zone_target_co2 is not None:
            self.coordinator.reconciler.set_zone(
                zone.guid, {"mode": ZoneMode.MANUAL, "co2": zone_target_co2}
            )
        speed = output.speed if output.is_on else 0
        self.coordinator.reconciler.set_breezer(
            self.breezer_guid, {"is_on": output.is_on, "speed": speed}
        )

    def pause(self, status: str) -> None:
        """Pause updates without disarming PID."""
        self.error = None
        self.output_speed = None
        self._set_status(status)

    def _set_status(self, status: str) -> None:
        """Update runtime status timestamp."""
        if self.status != status:
            _LOGGER.debug(
                "%s: local PID status changed: %s -> %s",
                self.manager.breezer_name(self.breezer_guid),
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
                "%s: cannot arm local PID because PID is not configured",
                self.breezer_name(breezer_guid),
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
                "%s: ignoring local PID target CO2 because PID is not configured",
                self.breezer_name(breezer_guid),
            )
            return
        controller.set_target_co2(target_co2)

    def extra_state_attributes(self, breezer_guid: str) -> dict[str, Any]:
        """Return PID attributes for a climate entity."""
        controller = self._controllers.get(breezer_guid)
        if controller is not None and self.is_configured(breezer_guid):
            return controller.extra_state_attributes()

        return {
            "pid_active": False,
            "pid_status": PID_STATUS_INACTIVE,
        }

    def breezer_name(
        self, breezer_guid: str, device: TionZoneDevice | None = None
    ) -> str:
        """Return a human-readable breezer name for logs."""
        if device is not None and device.name:
            return device.name

        if found_device := self.coordinator.get_device(breezer_guid):
            return found_device.name or breezer_guid

        return breezer_guid

    def write_all(self, data: TionData) -> None:
        """Write desired state for all active breezers into the reconciler."""
        for breezer_guid, controller in list(self._controllers.items()):
            if not controller.active:
                continue
            try:
                controller.evaluate(data)
            except (
                Exception
            ):  # broad catch is intentional; isolate per-breezer failures
                _LOGGER.exception(
                    "%s: unexpected error evaluating local PID",
                    self.breezer_name(breezer_guid),
                )

    def _pid_options(self, breezer_guid: str) -> dict[str, Any]:
        """Return stored PID options for a breezer."""
        return self.entry.options.get(CONF_PID_BREEZERS, {}).get(breezer_guid, {})

    def _controller(self, breezer_guid: str) -> _TionBreezerPidController | None:
        """Return a configured PID controller for a breezer."""
        if not self.is_configured(breezer_guid):
            return None

        controller = self._controllers.get(breezer_guid)
        if controller is None:
            _LOGGER.debug(
                "%s: creating local PID controller",
                self.breezer_name(breezer_guid),
            )
            controller = _TionBreezerPidController(self, breezer_guid)
            self._controllers[breezer_guid] = controller
        return controller
