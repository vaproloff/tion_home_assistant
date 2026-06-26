"""Tests for Tion local PID runtime manager."""

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tion.client import TionZoneDevice
from custom_components.tion.const import (
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    ZoneMode,
)
from custom_components.tion.pid_manager import (
    PID_STATUS_INACTIVE,
    PID_STATUS_NOT_CONFIGURED,
    PID_STATUS_PAUSED_SENSOR_UNAVAILABLE,
    PID_STATUS_RUNNING,
    TionPidManager,
)

SENSOR_ENTITY_ID = "sensor.external_co2"
BREEZER_GUID = "breezer-guid"


def _pid_options(*, enabled: bool = True) -> dict:
    """Return fake local PID options."""
    return {
        CONF_PID_BREEZERS: {
            BREEZER_GUID: {
                CONF_PID_ENABLED: enabled,
                CONF_CO2_SENSOR_ENTITY_ID: SENSOR_ENTITY_ID,
                CONF_PID_BASE_OUTPUT: 20.0,
                CONF_PID_KP: 0.5,
                CONF_PID_KI: 0.0,
                CONF_PID_KD: 0.0,
            }
        }
    }


class FakeStates:
    """Fake Home Assistant state machine."""

    def __init__(self, state: str | None) -> None:
        """Initialize fake states."""
        self._state = state

    def get(self, entity_id: str) -> SimpleNamespace | None:
        """Return a fake state."""
        if entity_id != SENSOR_ENTITY_ID or self._state is None:
            return None
        return SimpleNamespace(state=self._state)


class FakeLoop:
    """Fake event loop clock."""

    def time(self) -> float:
        """Return current monotonic time."""
        return 10.0


class FakeHass:
    """Fake Home Assistant object."""

    def __init__(self, sensor_state: str | None) -> None:
        """Initialize fake Home Assistant."""
        self.states = FakeStates(sensor_state)
        self.loop = FakeLoop()
        self.created_tasks = 0

    def async_create_task(self, coro: Any, *args: Any, **kwargs: Any) -> None:
        """Record a scheduled task and discard the coroutine."""
        coro.close()
        self.created_tasks += 1


class FakeConfigEntry:
    """Fake config entry."""

    entry_id = "entry-id"

    def __init__(self, options: dict | None = None) -> None:
        """Initialize fake config entry."""
        self.options = options or _pid_options()


class FakeDisabledConfigEntry(FakeConfigEntry):
    """Fake config entry with stored but disabled PID options."""

    def __init__(self) -> None:
        """Initialize fake config entry."""
        super().__init__(_pid_options(enabled=False))


class FakeReconciler:
    """Fake reconciler recording desired-state writes."""

    def __init__(self) -> None:
        """Initialize the fake reconciler."""
        self.breezer: dict[str, dict[str, Any]] = {}
        self.zone: dict[str, dict[str, Any]] = {}

    def set_breezer(self, guid: str, fields: dict[str, Any]) -> None:
        """Record a breezer desired-state write."""
        self.breezer.setdefault(guid, {}).update(fields)

    def set_zone(self, guid: str, fields: dict[str, Any]) -> None:
        """Record a zone desired-state write."""
        self.zone[guid] = dict(fields)

    def current_breezer(self, guid: str) -> dict[str, Any]:
        """Return a copy of the breezer's current desired overlay."""
        return dict(self.breezer.get(guid, {}))


class FakeCoordinator:
    """Fake Tion coordinator."""

    def __init__(
        self, device: TionZoneDevice, zone_mode: ZoneMode = ZoneMode.MANUAL
    ) -> None:
        """Initialize fake coordinator."""
        self.device = device
        self.reconciler = FakeReconciler()
        self.zone = SimpleNamespace(
            guid="zone-guid",
            name="Zone",
            valid=True,
            mode=SimpleNamespace(
                current=zone_mode,
                auto_set=SimpleNamespace(co2=800),
            ),
        )

    def get_device(self, guid: str) -> TionZoneDevice | None:
        """Return the fake breezer by guid."""
        return self.device if guid == self.device.guid else None

    async def async_request_refresh(self) -> None:
        """Provide the refresh hook armed PID kicks on start."""


class FakeData:
    """Fake coordinator data exposing device and zone lookups."""

    def __init__(self, device: TionZoneDevice, zone: Any) -> None:
        """Initialize fake coordinator data."""
        self._device = device
        self._zone = zone

    def device(self, guid: str) -> TionZoneDevice | None:
        """Return the fake breezer."""
        return self._device if guid == BREEZER_GUID else None

    def zone(self, guid: str) -> Any:
        """Return the fake zone."""
        return self._zone if guid == BREEZER_GUID else None


def _device(*, speed: int = 1, is_on: bool = True) -> TionZoneDevice:
    """Return a fake Tion breezer."""
    return TionZoneDevice(
        {
            "guid": BREEZER_GUID,
            "name": "Breezer",
            "type": "breezer4",
            "max_speed": 6,
            "is_online": True,
            "data": {
                "data_valid": True,
                "is_on": is_on,
                "speed": speed,
                "speed_min_set": 0,
                "speed_max_set": 6,
                "t_set": 20,
                "heater_enabled": False,
                "heater_mode": "maintenance",
                "gate": 0,
            },
        }
    )


def _data(coordinator: FakeCoordinator) -> FakeData:
    """Build coordinator data exposing the coordinator's device and zone."""
    return FakeData(coordinator.device, coordinator.zone)


def _armed_manager(coordinator: FakeCoordinator) -> TionPidManager:
    """Build a manager with an armed controller for the fake breezer."""
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)
    return manager


def test_breezer_name_uses_device_and_coordinator_contract() -> None:
    """Test breezer log names use the explicit device or coordinator lookup."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)

    assert manager.breezer_name(BREEZER_GUID, _device(speed=1)) == "Breezer"
    assert manager.breezer_name(BREEZER_GUID) == "Breezer"

    coordinator.get_device = lambda guid: SimpleNamespace(guid=guid, name=None)  # type: ignore[method-assign]

    assert manager.breezer_name(BREEZER_GUID) == BREEZER_GUID


def test_pid_manager_arming_requests_immediate_refresh() -> None:
    """Test arming local PID kicks an immediate coordinator refresh."""
    hass = FakeHass("1000")
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(hass, FakeConfigEntry(), coordinator)

    assert manager.start_breezer_pid(BREEZER_GUID) is True

    assert manager.has_active_pid() is True
    assert hass.created_tasks == 1


def test_pid_manager_does_not_arm_without_enabled_pid() -> None:
    """Test disabled PID options do not arm a controller."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeDisabledConfigEntry(), coordinator)

    unsubscribe = manager.async_start()

    assert manager.has_active_pid() is False
    assert manager.start_breezer_pid(BREEZER_GUID) is False
    assert manager._controllers == {}  # noqa: SLF001

    unsubscribe()


def test_pid_manager_async_stop_disarms_controllers() -> None:
    """Test the unload callback disarms active controllers."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    unsubscribe = manager.async_start()
    manager.start_breezer_pid(BREEZER_GUID)
    assert manager.has_active_pid() is True

    unsubscribe()

    assert manager.has_active_pid() is False


def test_pid_manager_extra_attributes_default_for_unconfigured_pid() -> None:
    """Test PID attributes stay lightweight when no controller exists."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeDisabledConfigEntry(), coordinator)

    assert manager.extra_state_attributes(BREEZER_GUID) == {
        "pid_active": False,
        "pid_status": PID_STATUS_INACTIVE,
    }


def test_write_all_writes_breezer_desired_for_changed_output() -> None:
    """Test a valid tick writes the desired breezer state without side effects."""
    device = _device(speed=1)
    coordinator = FakeCoordinator(device)
    manager = _armed_manager(coordinator)

    manager.write_all(_data(coordinator))

    assert coordinator.reconciler.breezer[BREEZER_GUID] == {"is_on": True, "speed": 6}
    assert coordinator.reconciler.zone == {}
    # Planner is pure: no snapshot mutation.
    assert device.data.speed == 1
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"] == PID_STATUS_RUNNING
    )


def test_write_all_uses_desired_speed_limits_over_reported() -> None:
    """Test PID honors the desired auto limits immediately, before cloud confirms.

    A just-changed max speed lives in the desired overlay while the reported
    device still carries the old limit. PID must clamp against the desired
    limit so the change takes effect on the next tick, not one cycle later.
    """
    device = _device(speed=1)  # reported speed_max_set=6
    coordinator = FakeCoordinator(device)
    coordinator.reconciler.set_breezer(BREEZER_GUID, {"speed_max_set": 2})
    manager = _armed_manager(coordinator)

    manager.write_all(_data(coordinator))

    # High CO2 would peg the speed at 6, but the desired max clamps it to 2.
    assert coordinator.reconciler.breezer[BREEZER_GUID]["speed"] == 2


def test_pid_manager_extra_attributes_omit_calculation_details() -> None:
    """Test PID calculation details stay out of persisted attributes."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)

    manager.write_all(_data(coordinator))

    assert manager.extra_state_attributes(BREEZER_GUID) == {
        "pid_active": True,
        "pid_status": PID_STATUS_RUNNING,
    }


def test_write_all_logs_pid_calculation_with_breezer_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test PID calculation details are logged with the breezer name."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)
    caplog.set_level(logging.DEBUG, logger="custom_components.tion.pid_manager")

    manager.write_all(_data(coordinator))

    assert "Breezer: PID calculation" in caplog.text
    assert BREEZER_GUID not in caplog.text
    assert "p=100.0" in caplog.text
    assert "i=0.0" in caplog.text
    assert "d=0.0" in caplog.text
    assert "raw_output=120.0" in caplog.text
    assert "min_speed=0" in caplog.text
    assert "max_speed=6" in caplog.text
    assert "pid_output_speed=6" in caplog.text


def test_write_all_still_writes_desired_for_unchanged_output() -> None:
    """Test an unchanged output still writes desired state; reconciler decides sends."""
    device = _device(speed=6)
    coordinator = FakeCoordinator(device)
    manager = _armed_manager(coordinator)

    manager.write_all(_data(coordinator))

    assert coordinator.reconciler.breezer[BREEZER_GUID] == {"is_on": True, "speed": 6}
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"] == PID_STATUS_RUNNING
    )


def test_write_all_writes_nothing_on_invalid_sensor_state() -> None:
    """Test an unavailable CO2 sensor writes no desired state and pauses."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("unknown"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    manager.write_all(_data(coordinator))

    assert coordinator.reconciler.breezer == {}
    assert coordinator.reconciler.zone == {}
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"]
        == PID_STATUS_PAUSED_SENSOR_UNAVAILABLE
    )


def test_write_all_writes_zone_manual_for_auto_zone() -> None:
    """Test an AUTO zone is driven back to MANUAL via a zone desired write."""
    coordinator = FakeCoordinator(_device(speed=6), zone_mode=ZoneMode.AUTO)
    manager = _armed_manager(coordinator)

    manager.write_all(_data(coordinator))

    assert coordinator.reconciler.zone["zone-guid"] == {
        "mode": ZoneMode.MANUAL,
        "co2": 800,
    }
    assert coordinator.reconciler.breezer[BREEZER_GUID] == {"is_on": True, "speed": 6}


def test_write_all_writes_zero_speed_when_pid_turns_off() -> None:
    """Test PID turning the breezer off writes speed 0 (off implies zero)."""
    coordinator = FakeCoordinator(_device(speed=4))
    manager = _armed_manager(coordinator)
    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    controller.controller.calculate = lambda **kwargs: SimpleNamespace(  # type: ignore[method-assign]
        error=-50.0,
        speed=3,
        is_on=False,
        p_output=0.0,
        i_output=0.0,
        d_output=0.0,
        raw_output=0.0,
    )

    manager.write_all(_data(coordinator))

    assert coordinator.reconciler.breezer[BREEZER_GUID] == {"is_on": False, "speed": 0}


def test_write_all_advances_pid_state_and_resets_on_disarm() -> None:
    """Test writing advances PID core state, which disarming then resets."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)

    manager.write_all(_data(coordinator))
    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    manager.stop_breezer_pid(BREEZER_GUID)

    assert controller.controller.state.last_error is None
    assert controller.controller.state.i_output == 0.0
    assert manager.has_active_pid() is False


def test_write_all_deactivates_unconfigured() -> None:
    """Test writing deactivates controllers that are no longer configured."""
    coordinator = FakeCoordinator(_device(speed=1))
    entry = FakeConfigEntry()
    manager = TionPidManager(FakeHass("1000"), entry, coordinator)
    manager.start_breezer_pid(BREEZER_GUID)
    entry.options[CONF_PID_BREEZERS][BREEZER_GUID][CONF_PID_ENABLED] = False

    manager.write_all(_data(coordinator))

    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    assert coordinator.reconciler.breezer == {}
    assert controller.active is False
    assert controller.status == PID_STATUS_NOT_CONFIGURED
    assert manager.has_active_pid() is False


def test_write_all_isolates_per_breezer_failures() -> None:
    """Test that an unexpected exception in one breezer does not abort writing."""

    class RaisingData(FakeData):
        """Coordinator data whose zone() always raises an unexpected error."""

        def zone(self, guid: str) -> None:
            raise RuntimeError("unexpected device data failure")

    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    # Must not raise; the exception is swallowed and logged, writing nothing.
    manager.write_all(RaisingData(coordinator.device, coordinator.zone))

    assert coordinator.reconciler.breezer == {}
