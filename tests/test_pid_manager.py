"""Tests for Tion local PID runtime manager."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

from custom_components.tion.client import TionError, TionZoneDevice
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
from custom_components.tion.pid_intent import BreezerCommand, PidIntent, ZoneCommand
from custom_components.tion.pid_manager import (
    PID_STATUS_INACTIVE,
    PID_STATUS_NOT_CONFIGURED,
    PID_STATUS_PAUSED_SENSOR_UNAVAILABLE,
    PID_STATUS_RUNNING,
    PID_STATUS_SEND_FAILED,
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
        self.background_tasks: list[Any] = []

    def async_create_background_task(
        self, hass: Any, target: Any, name: str, **kwargs: Any
    ) -> SimpleNamespace:
        """Record a scheduled background coroutine without running it."""
        self.background_tasks.append(target)
        return SimpleNamespace(name=name)


class FakeDisabledConfigEntry(FakeConfigEntry):
    """Fake config entry with stored but disabled PID options."""

    def __init__(self) -> None:
        """Initialize fake config entry."""
        super().__init__(_pid_options(enabled=False))


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


def _breezer_command() -> BreezerCommand:
    """Build a breezer command matching the fake device."""
    return BreezerCommand(
        guid=BREEZER_GUID,
        is_on=True,
        speed=6,
        t_set=20,
        speed_min_set=0,
        speed_max_set=6,
        heater_enabled=False,
        heater_mode="maintenance",
        gate=0,
    )


def _armed_manager(coordinator: FakeCoordinator) -> TionPidManager:
    """Build a manager with an armed controller for the fake breezer."""
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)
    return manager


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
    manager = TionPidManager(FakeHass("1000"), FakeDisabledConfigEntry(), coordinator)

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
    manager = TionPidManager(FakeHass("1000"), FakeDisabledConfigEntry(), coordinator)

    assert manager.extra_state_attributes(BREEZER_GUID) == {
        "pid_active": False,
        "pid_source_entity_id": SENSOR_ENTITY_ID,
        "pid_source_co2": None,
        "pid_error": None,
        "pid_output_speed": None,
        "pid_status": PID_STATUS_INACTIVE,
        "pid_last_update": None,
    }


def test_plan_breezer_returns_breezer_command_for_changed_output() -> None:
    """Test a valid tick plans a changed breezer command without side effects."""
    device = _device(speed=1)
    coordinator = FakeCoordinator(device)
    manager = _armed_manager(coordinator)

    intent = manager.plan_breezer(BREEZER_GUID, _data(coordinator))

    assert intent is not None
    assert intent.breezer_command is not None
    assert intent.breezer_command.speed == 6
    assert intent.breezer_command.is_on is True
    assert intent.zone_command is None
    # Planner is pure: no network I/O and no snapshot mutation.
    assert coordinator.zone_commands == []
    assert coordinator.commands == []
    assert device.data.speed == 1
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"] == PID_STATUS_RUNNING
    )


def test_plan_breezer_returns_none_for_unchanged_manual_output() -> None:
    """Test an unchanged MANUAL output plans nothing but still reports RUNNING."""
    device = _device(speed=6)
    coordinator = FakeCoordinator(device)
    manager = _armed_manager(coordinator)

    intent = manager.plan_breezer(BREEZER_GUID, _data(coordinator))

    assert intent is None
    assert device.data.speed == 6
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"] == PID_STATUS_RUNNING
    )


def test_plan_breezer_returns_none_on_invalid_sensor_state() -> None:
    """Test an unavailable CO2 sensor plans nothing and pauses."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("unknown"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    intent = manager.plan_breezer(BREEZER_GUID, _data(coordinator))

    assert intent is None
    assert coordinator.zone_commands == []
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"]
        == PID_STATUS_PAUSED_SENSOR_UNAVAILABLE
    )


def test_plan_breezer_plans_zone_command_for_auto_zone() -> None:
    """Test an AUTO zone is planned back to MANUAL via a zone command."""
    coordinator = FakeCoordinator(_device(speed=6), zone_mode=ZoneMode.AUTO)
    manager = _armed_manager(coordinator)

    intent = manager.plan_breezer(BREEZER_GUID, _data(coordinator))

    assert intent is not None
    assert intent.zone_command == ZoneCommand(guid="zone-guid", co2=800)
    # speed unchanged (6 -> 6), so no breezer command and no I/O from the planner.
    assert intent.breezer_command is None
    assert coordinator.zone_commands == []


def test_plan_breezer_advances_pid_state_and_resets_on_disarm() -> None:
    """Test planning advances PID core state, which disarming then resets."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)

    intent = manager.plan_breezer(BREEZER_GUID, _data(coordinator))
    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    manager.stop_breezer_pid(BREEZER_GUID)

    assert intent is not None
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

    asyncio.run(manager.async_evaluate_all(_data(coordinator)))

    controller = manager._controllers[BREEZER_GUID]  # noqa: SLF001
    assert controller.active is False
    assert controller.status == PID_STATUS_NOT_CONFIGURED
    assert manager.has_active_pid() is False


def test_pid_manager_evaluate_all_isolates_per_breezer_failures() -> None:
    """Test that an unexpected exception in one breezer does not abort the loop."""

    class RaisingData(FakeData):
        """Coordinator data whose zone() always raises an unexpected error."""

        def zone(self, guid: str) -> None:
            raise RuntimeError("unexpected device data failure")

    coordinator = FakeCoordinator(_device(speed=1))
    manager = TionPidManager(FakeHass("1000"), FakeConfigEntry(), coordinator)
    manager.start_breezer_pid(BREEZER_GUID)

    # Must not raise; the exception must be swallowed and logged.
    asyncio.run(
        manager.async_evaluate_all(RaisingData(coordinator.device, coordinator.zone))
    )


def test_async_execute_sends_zone_then_breezer() -> None:
    """Test execute dispatches the zone command before the breezer command."""
    coordinator = FakeCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)
    intent = PidIntent(
        breezer_guid=BREEZER_GUID,
        zone_command=ZoneCommand(guid="zone-guid", co2=800),
        breezer_command=_breezer_command(),
    )

    asyncio.run(manager.async_execute(intent))

    assert coordinator.zone_commands == [
        {
            "guid": "zone-guid",
            "mode": ZoneMode.MANUAL,
            "co2": 800,
            "request_refresh": False,
            "track_stale": False,
        }
    ]
    assert coordinator.commands == [
        {**asdict(_breezer_command()), "request_refresh": False, "track_stale": False}
    ]


def test_async_execute_zone_failure_skips_breezer_and_pauses() -> None:
    """Test a failed zone command stops the breezer send and pauses PID."""

    class _ZoneFailCoordinator(FakeCoordinator):
        async def async_send_zone(self, **kwargs: Any) -> bool:
            raise TionError("zone boom")

    coordinator = _ZoneFailCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)
    intent = PidIntent(
        breezer_guid=BREEZER_GUID,
        zone_command=ZoneCommand(guid="zone-guid", co2=800),
        breezer_command=_breezer_command(),
    )

    asyncio.run(manager.async_execute(intent))

    assert coordinator.commands == []
    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"]
        == PID_STATUS_SEND_FAILED
    )


def test_async_execute_breezer_failure_pauses() -> None:
    """Test a failed breezer command pauses PID."""

    class _BreezerFailCoordinator(FakeCoordinator):
        async def async_send_breezer(self, **kwargs: Any) -> bool:
            raise TionError("breezer boom")

    coordinator = _BreezerFailCoordinator(_device(speed=1))
    manager = _armed_manager(coordinator)
    intent = PidIntent(breezer_guid=BREEZER_GUID, breezer_command=_breezer_command())

    asyncio.run(manager.async_execute(intent))

    assert (
        manager.extra_state_attributes(BREEZER_GUID)["pid_status"]
        == PID_STATUS_SEND_FAILED
    )


def test_schedule_intent_backgrounds_execution() -> None:
    """Test schedule_intent enqueues a background task instead of awaiting."""
    coordinator = FakeCoordinator(_device(speed=1))
    entry = FakeConfigEntry()
    manager = TionPidManager(FakeHass("1000"), entry, coordinator)
    manager.start_breezer_pid(BREEZER_GUID)
    intent = PidIntent(breezer_guid=BREEZER_GUID, breezer_command=_breezer_command())

    manager.schedule_intent(intent)

    assert coordinator.commands == []
    assert len(entry.background_tasks) == 1

    async def _drain() -> None:
        for coro in entry.background_tasks:
            await coro

    asyncio.run(_drain())
    assert coordinator.commands == [
        {**asdict(_breezer_command()), "request_refresh": False, "track_stale": False}
    ]
