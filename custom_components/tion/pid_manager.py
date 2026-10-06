"""Local PID control of Tion breezers by a CO2 sensor of Home Assistant."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from functools import partial
import logging
import math
import time
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .api import Breezer, TionError, view
from .const import (
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_INTERVAL,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    CONF_PID_MAX_SPEED,
    CONF_PID_MIN_SPEED,
    CONF_PID_TARGET_CO2,
    DEFAULT_PID_BASE_OUTPUT,
    DEFAULT_PID_INTERVAL,
    DEFAULT_PID_KD,
    DEFAULT_PID_KI,
    DEFAULT_PID_KP,
    DEFAULT_PID_MIN_SPEED,
    DEFAULT_TARGET_CO2,
    MODEL_NAMES,
    PidStatus,
)
from .coordinator import TionConfigEntry, TionCoordinator
from .pid import PidCoefficients, PidController, PidOutput

_LOGGER = logging.getLogger(__name__)

# Top speed when the breezer does not report one.
FALLBACK_SPEED_MAX = 6


def is_pid_set_up(pid_options: Mapping[str, Any]) -> bool:
    """Return whether PID options turn local PID on with a sensor chosen."""
    return bool(
        pid_options.get(CONF_PID_ENABLED) and pid_options.get(CONF_CO2_SENSOR_ENTITY_ID)
    )


class _BreezerPid:
    """The local PID of one breezer."""

    def __init__(self, pid_options: Mapping[str, Any]) -> None:
        self.controller = PidController(
            PidCoefficients(
                kp=float(pid_options.get(CONF_PID_KP, DEFAULT_PID_KP)),
                ki=float(pid_options.get(CONF_PID_KI, DEFAULT_PID_KI)),
                kd=float(pid_options.get(CONF_PID_KD, DEFAULT_PID_KD)),
                base_output=float(
                    pid_options.get(CONF_PID_BASE_OUTPUT, DEFAULT_PID_BASE_OUTPUT)
                ),
            )
        )
        self.sensor: str = pid_options[CONF_CO2_SENSOR_ENTITY_ID]
        self.interval = timedelta(
            seconds=int(pid_options.get(CONF_PID_INTERVAL, DEFAULT_PID_INTERVAL))
        )
        self.active = False
        self.status = PidStatus.INACTIVE
        self.preset_limits: tuple[int, int] | None = None
        self.unsub_timer: CALLBACK_TYPE | None = None
        self.stepping = False
        self.listeners: list[Callable[[], None]] = []


class TionPidManager:
    """Run local PID for the breezers of one config entry that have it set up."""

    def __init__(
        self, hass: HomeAssistant, entry: TionConfigEntry, coordinator: TionCoordinator
    ) -> None:
        """Create a PID for each breezer whose options set it up."""
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._pids = {
            breezer_id: _BreezerPid(pid_options)
            for breezer_id, pid_options in entry.options.get(
                CONF_PID_BREEZERS, {}
            ).items()
            if is_pid_set_up(pid_options)
        }
        self._unsub_coordinator = coordinator.async_add_listener(
            self._handle_coordinator_update
        )

    def is_configured(self, breezer_id: str) -> bool:
        """Return whether local PID is set up for the breezer."""
        return breezer_id in self._pids

    def is_active(self, breezer_id: str) -> bool:
        """Return whether local PID drives the breezer now."""
        return (pid := self._pids.get(breezer_id)) is not None and pid.active

    def status(self, breezer_id: str) -> PidStatus:
        """Return what the breezer's local PID is doing."""
        pid = self._pids.get(breezer_id)
        return pid.status if pid is not None else PidStatus.INACTIVE

    @callback
    def start(self, breezer_id: str) -> None:
        """Let local PID drive the breezer; a running PID just steps now."""
        if (pid := self._pids.get(breezer_id)) is None:
            raise ValueError("Local PID is not set up for this breezer")
        if not pid.active:
            pid.active = True
            pid.controller.reset()
            pid.unsub_timer = async_track_time_interval(
                self._hass,
                partial(self._scheduled_step, breezer_id),
                pid.interval,
                name="Tion local PID step",
            )
            pid.status = PidStatus.RUNNING
            self._notify(pid)
        self._schedule_step(breezer_id)

    @callback
    def stop(self, breezer_id: str) -> None:
        """Stop local PID of the breezer, if it runs."""
        if (pid := self._pids.get(breezer_id)) is None or not pid.active:
            return
        pid.active = False
        if pid.unsub_timer is not None:
            pid.unsub_timer()
            pid.unsub_timer = None
        pid.controller.reset()
        pid.status = PidStatus.INACTIVE
        self._notify(pid)

    def limits(self, breezer_id: str) -> tuple[int, int]:
        """Return the breezer's own PID speed limits."""
        pid_options = self._pid_options(breezer_id)
        return (
            int(pid_options.get(CONF_PID_MIN_SPEED, DEFAULT_PID_MIN_SPEED)),
            int(pid_options.get(CONF_PID_MAX_SPEED, self._speed_max(breezer_id))),
        )

    def effective_limits(self, breezer_id: str) -> tuple[int, int]:
        """Return the limits PID keeps to: the active preset's, else its own."""
        pid = self._pids.get(breezer_id)
        if pid is not None and pid.preset_limits is not None:
            return pid.preset_limits
        return self.limits(breezer_id)

    def set_limits(
        self,
        breezer_id: str,
        *,
        speed_min: int | None = None,
        speed_max: int | None = None,
    ) -> None:
        """Save new own PID speed limits and step with them."""
        changes = {
            key: value
            for key, value in (
                (CONF_PID_MIN_SPEED, speed_min),
                (CONF_PID_MAX_SPEED, speed_max),
            )
            if value is not None
        }
        self._save(breezer_id, changes)
        self._step_if_active(breezer_id)

    def target(self, breezer_id: str) -> int:
        """Return the CO2 level the breezer's PID aims at, ppm."""
        return int(
            self._pid_options(breezer_id).get(CONF_PID_TARGET_CO2, DEFAULT_TARGET_CO2)
        )

    def set_target(self, breezer_id: str, value: int) -> None:
        """Save a new CO2 target, restart the integral and step with it."""
        self._save(breezer_id, {CONF_PID_TARGET_CO2: value})
        if (pid := self._pids.get(breezer_id)) is not None:
            pid.controller.reset()
        self._step_if_active(breezer_id)

    def set_preset_limits(
        self, breezer_id: str, limits: tuple[int, int] | None
    ) -> None:
        """Make PID keep to a preset's limits; None returns to its own."""
        if (pid := self._pids.get(breezer_id)) is None:
            return
        pid.preset_limits = limits
        self._step_if_active(breezer_id)

    @callback
    def add_listener(
        self, breezer_id: str, listener: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Call listener when the breezer's PID starts, stops or changes status."""
        if (pid := self._pids.get(breezer_id)) is None:
            return lambda: None
        pid.listeners.append(listener)
        return lambda: pid.listeners.remove(listener)

    @callback
    def async_stop(self) -> None:
        """Stop every PID and stop following the coordinator."""
        self._unsub_coordinator()
        for breezer_id in self._pids:
            self.stop(breezer_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        # The room's cloud auto mode drives every breezer of the room; PID yields.
        for breezer_id, pid in self._pids.items():
            if pid.active and self._room_auto_enabled(breezer_id):
                self.stop(breezer_id)

    @callback
    def _scheduled_step(self, breezer_id: str, _now: datetime) -> None:
        self._schedule_step(breezer_id)

    @callback
    def _step_if_active(self, breezer_id: str) -> None:
        if self.is_active(breezer_id):
            self._schedule_step(breezer_id)

    @callback
    def _schedule_step(self, breezer_id: str) -> None:
        self._entry.async_create_background_task(
            self._hass, self._async_step(breezer_id), "tion_pid_step"
        )

    async def _async_step(self, breezer_id: str) -> None:
        pid = self._pids[breezer_id]
        if not pid.active or pid.stepping:
            return
        pid.stepping = True
        try:
            status = await self._async_regulate(breezer_id, pid)
        finally:
            pid.stepping = False
        if pid.active and status is not pid.status:
            pid.status = status
            self._notify(pid)

    async def _async_regulate(self, breezer_id: str, pid: _BreezerPid) -> PidStatus:
        if not self._coordinator.device_available(breezer_id):
            return PidStatus.PAUSED_DEVICE_UNAVAILABLE
        breezer = self._breezer(breezer_id)
        if (
            breezer is None
            or breezer.speed_max is None
            or breezer.speed is None
            or breezer.is_on is None
        ):
            return PidStatus.PAUSED_INVALID_DEVICE_DATA
        if (co2 := self._sensor_value(pid.sensor)) is None:
            return PidStatus.PAUSED_SENSOR_UNAVAILABLE
        speed_min, speed_max = self.effective_limits(breezer_id)
        output = pid.controller.calculate(
            source_co2=co2,
            target_co2=self.target(breezer_id),
            speed_min=speed_min,
            speed_max=speed_max,
            device_max_speed=breezer.speed_max,
            now=time.monotonic(),
        )
        if not (changes := _command_changes(breezer, output)):
            return PidStatus.RUNNING
        try:
            await self._coordinator.cloud.async_command(breezer.command(**changes))
        except TionError as err:
            # No name or ID in the log; the error text names the device.
            _LOGGER.warning(
                "Local PID could not command a %s (%s); it retries at its next step",
                MODEL_NAMES.get(breezer.device.product_id, "breezer"),
                type(err).__name__,
            )
            return PidStatus.SEND_FAILED
        return PidStatus.RUNNING

    def _sensor_value(self, entity_id: str) -> float | None:
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def _breezer(self, breezer_id: str) -> Breezer | None:
        device = self._coordinator.data.device(breezer_id)
        device_view = view(device) if device is not None else None
        return device_view if isinstance(device_view, Breezer) else None

    def _speed_max(self, breezer_id: str) -> int:
        breezer = self._breezer(breezer_id)
        if breezer is None or breezer.speed_max is None:
            return FALLBACK_SPEED_MAX
        return breezer.speed_max

    def _room_auto_enabled(self, breezer_id: str) -> bool:
        account = self._coordinator.data
        if (device := account.device(breezer_id)) is None:
            return False
        room = account.room_of(device)
        auto = room.configured_auto if room is not None else None
        return auto is not None and auto.enabled

    def _pid_options(self, breezer_id: str) -> Mapping[str, Any]:
        return self._entry.options.get(CONF_PID_BREEZERS, {}).get(breezer_id, {})

    def _save(self, breezer_id: str, changes: Mapping[str, Any]) -> None:
        options = dict(self._entry.options)
        pid_breezers = dict(options.get(CONF_PID_BREEZERS, {}))
        pid_breezers[breezer_id] = {**pid_breezers.get(breezer_id, {}), **changes}
        self._hass.config_entries.async_update_entry(
            self._entry, options={**options, CONF_PID_BREEZERS: pid_breezers}
        )

    @staticmethod
    def _notify(pid: _BreezerPid) -> None:
        for listener in list(pid.listeners):
            listener()


def _command_changes(breezer: Breezer, output: PidOutput) -> dict[str, Any]:
    """Return what to send so the breezer runs as PID says; empty when it does."""
    if not output.is_on:
        return {"is_on": False} if breezer.is_on else {}
    changes: dict[str, Any] = {}
    if not breezer.is_on:
        changes["is_on"] = True
    if breezer.speed != output.speed:
        changes["speed"] = output.speed
    return changes
