"""Tests for Tion local PID runtime manager."""

import asyncio
from types import SimpleNamespace
from typing import Any

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

# PID reads device/zone through the coordinator, which ignores this argument in
# the fakes below; a sentinel keeps call sites explicit.
DATA = SimpleNamespace(locations=[])


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


class FakeDisabledConfigEntry:
    """Fake config entry with stored but disabled PID options."""

    entry_id = "entry-id"

    def __init__(self) -> None:
        """Initialize fake config entry."""
        self.options = _pid_options(enabled=False)


class FakeCoordinator:
    """Fake Tion coordinator."""

    def __init__(
        self, device: TionZoneDevice, zone_mode: ZoneMode = ZoneMode.MANUAL
    ) -> None:
        """Initialize fake coordinator."""
        self.device = device
        self.commands: list[dict[str, Any]] = []
        self.zone_commands: list[dict[str, Any]] = []
        self.refresh_requests = 0
        self.zone = SimpleNamespace(
            guid="zone-guid",
            name="Zone",
            valid=True,
            mode=SimpleNamespace(
                current=zone_mode,
                auto_set=SimpleNamespace(co2=800),
            ),
        )

    def get_device(
        self, guid: str, data: Any = None
    ) -> TionZoneDevice | None:
        """Return the fake breezer."""
        return self.device if guid == BREEZER_GUID else None

    def get_device_zone(
        self, guid: str, data: Any = None
    ) -> SimpleNamespace | None:
        """Return the fake zone."""
        return self.zone if guid == BREEZER_GUID else None

    async def async_send_breezer(self, **kwargs: Any) -> bool:
        """Record a fake breezer command."""
        self.commands.append(kwargs)
        return True

    async def async_send_zone(self, **kwargs: Any) -> bool:
        """Record a fake zone command."""
        self.zone_commands.append(kwargs)
        self.zone.mode.current = kwargs["mode"]
        return True

    async def async_request_refresh(self) -> None:
        """Record a refresh request."""
        self.refresh_requests += 1


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


def test_pid_manager_sends_breezer_command_for_changed_output() -> None:
    """Test a valid PID tick sends a changed speed command and updates locally."""
    device = _device(speed=1)
    coordinator = FakeCoordinator(device)
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    output = asyncio.run(manager.async_evaluate_breezer(BREEZER_GUID, DATA))

    assert output is not None
    assert output.speed == 6
    assert coordinator.zone_commands == []
    assert coordinator.commands == [
        {
            "guid": BREEZER_GUID,
            "is_on": True,
            "t_set": 20,
            "speed": 6,
            "speed_min_set": 0,
            "speed_max_set": 6,
            "heater_enabled": False,
            "heater_mode": "maintenance",
            "gate": 0,
            "request_refresh": False,
            "track_stale": False,
        }
    ]
    # Optimistic local update reflects the sent command.
    assert device.data.speed == 6
    assert device.data.is_on is True
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"] == PID_STATUS_RUNNING
    )


def test_pid_manager_arming_requests_immediate_refresh() -> None:
    """Test arming local PID kicks an immediate coordinator refresh."""
    hass = FakeHass("1000")
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(hass, FakeConfigEntry(), coordinator)

    assert manager.start_breezer_pid(BREEZER_GUID) is True

    assert manager.has_active_pid() is True
    assert hass.created_tasks == 1


def test_pid_manager_does_not_arm_without_enabled_pid() -> None:
    """Test disabled PID options do not arm a controller and report no enabled PID."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(
        FakeHass("1000"), FakeDisabledConfigEntry(), coordinator
    )

    unsubscribe = manager.async_start()

    assert manager.has_enabled_pid() is False
    assert manager.has_active_pid() is False
    assert manager.configured_breezers() == set()
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
    """Test PID attributes are stable when no runtime controller exists."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(
        FakeHass("1000"), FakeDisabledConfigEntry(), coordinator
    )

    assert manager.extra_state_attributes(BREEZER_GUID) == {
        "pid_active": False,
        "pid_source_entity_id": SENSOR_ENTITY_ID,
        "pid_source_co2": None,
        "pid_error": None,
        "pid_output_speed": None,
        "pid_status": PID_STATUS_INACTIVE,
        "pid_last_update": None,
    }


def test_pid_manager_pauses_on_invalid_sensor_state() -> None:
    """Test invalid external CO2 state pauses PID without a command."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("unknown"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    output = asyncio.run(manager.async_evaluate_breezer(BREEZER_GUID, DATA))

    assert output is None
    assert coordinator.zone_commands == []
    assert coordinator.commands == []
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"]
        == PID_STATUS_PAUSED_SENSOR_UNAVAILABLE
    )


def test_pid_manager_skips_unchanged_output() -> None:
    """Test unchanged calculated output does not send a command."""
    device = _device(speed=6)
    coordinator = FakeCoordinator(device)
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    output = asyncio.run(manager.async_evaluate_breezer(BREEZER_GUID, DATA))

    assert output is not None
    assert output.speed == 6
    assert coordinator.zone_commands == []
    assert coordinator.commands == []
    assert device.data.speed == 6


def test_pid_manager_disarm_resets_pid_core_state() -> None:
    """Test disarming local PID resets accumulated PID controller state."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    output = asyncio.run(manager.async_evaluate_breezer(BREEZER_GUID, DATA))
    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    manager.stop_breezer_pid(BREEZER_GUID)

    assert output is not None
    assert controller.controller.state.last_error is None
    assert controller.controller.state.i_output == 0.0
    assert manager.has_active_pid() is False


def test_pid_manager_evaluate_all_deactivates_unconfigured() -> None:
    """Test evaluation deactivates controllers that are no longer configured."""
    coordinator = FakeCoordinator(_device(speed=1))
    entry = FakeConfigEntry()
    manager = TionPidManager(FakeHass("1000"), entry, coordinator)
    manager.start_breezer_pid(BREEZER_GUID)
    entry.options[CONF_PID_BREEZERS][BREEZER_GUID][CONF_PID_ENABLED] = False

    asyncio.run(manager.async_evaluate_all(DATA))

    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    assert controller.active is False
    assert controller.status == PID_STATUS_NOT_CONFIGURED
    assert manager.has_active_pid() is False


def test_pid_manager_returns_auto_zone_to_manual() -> None:
    """Test active local PID disables MagicAir cloud auto mode."""
    coordinator = FakeCoordinator(_device(speed=6), zone_mode=ZoneMode.AUTO)
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    output = asyncio.run(manager.async_evaluate_breezer(BREEZER_GUID, DATA))

    assert output is not None
    assert coordinator.zone_commands == [
        {
            "guid": "zone-guid",
            "mode": ZoneMode.MANUAL,
            "co2": 800,
            "request_refresh": False,
            "track_stale": False,
        }
    ]
    assert coordinator.commands == []
