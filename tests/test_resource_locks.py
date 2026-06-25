"""Tests for serializing full Tion resource payload updates."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from custom_components.tion.const import TionDeviceType
from custom_components.tion.number import TionMaxSpeed
from custom_components.tion.switch import TionBreezerHeaterSwitch

BREEZER_GUID = "breezer-guid"


class FakeDeviceData:
    """Fake Tion device data."""

    data_valid = True
    is_on = True
    t_set = 20
    speed = 4
    speed_min_set = 1
    speed_max_set = 4
    heater_enabled = None
    heater_mode = "maintenance"
    heater_power = 0
    heater_installed = True
    heater_type = "ptc"
    gate = 0
    t_in = 18
    t_out = 22
    filter_time_seconds = 1000
    filter_need_replace = False


class FakeDevice:
    """Fake Tion breezer device."""

    guid = BREEZER_GUID
    name = "Breezer"
    type = TionDeviceType.BREEZER_4S
    is_online = True
    valid = True
    max_speed = 6

    def __init__(self) -> None:
        """Initialize fake device."""
        self.data = FakeDeviceData()


class FakeCoordinator:
    """Fake coordinator exposing a shared breezer command lock."""

    def __init__(self) -> None:
        """Initialize fake coordinator."""
        self.device = FakeDevice()
        self.last_update_success = True
        self.lock = asyncio.Lock()
        self.send_started = asyncio.Event()
        self.second_send_started = asyncio.Event()
        self.release_first_send = asyncio.Event()
        self.release_second_send = asyncio.Event()
        self.commands: list[dict[str, Any]] = []

    def get_device(self, guid: str) -> FakeDevice | None:
        """Return the fake breezer."""
        return self.device if guid == BREEZER_GUID else None

    @asynccontextmanager
    async def async_breezer_mode_command(self, guid: str):
        """Serialize a breezer read-modify-send command."""
        assert guid == BREEZER_GUID
        async with self.lock:
            yield

    async def async_send_breezer(self, **kwargs: Any) -> bool:
        """Record and apply a fake breezer command in a controlled order."""
        self.commands.append(kwargs.copy())
        if len(self.commands) == 1:
            self.send_started.set()
            await self.release_first_send.wait()
        else:
            self.second_send_started.set()
            await self.release_second_send.wait()

        self.device.data.is_on = kwargs["is_on"]
        self.device.data.t_set = kwargs["t_set"]
        self.device.data.speed = kwargs["speed"]
        self.device.data.speed_min_set = kwargs["speed_min_set"]
        self.device.data.speed_max_set = kwargs["speed_max_set"]
        self.device.data.heater_enabled = kwargs["heater_enabled"]
        self.device.data.heater_mode = kwargs["heater_mode"]
        self.device.data.gate = kwargs["gate"]
        return True


def _max_speed(coordinator: FakeCoordinator) -> TionMaxSpeed:
    """Return a max speed number bound to the fake coordinator."""
    entity = TionMaxSpeed.__new__(TionMaxSpeed)
    entity.coordinator = coordinator
    entity._device = coordinator.device  # noqa: SLF001
    entity._attr_native_max_value = coordinator.device.max_speed  # noqa: SLF001
    entity._attr_name = "Max speed"  # noqa: SLF001
    entity._attr_has_entity_name = False  # noqa: SLF001
    entity._attr_translation_key = None  # noqa: SLF001
    entity._breezer_max_speed = coordinator.device.data.speed_max_set  # noqa: SLF001
    return entity


def _heater(coordinator: FakeCoordinator) -> TionBreezerHeaterSwitch:
    """Return a heater switch bound to the fake coordinator."""
    entity = TionBreezerHeaterSwitch.__new__(TionBreezerHeaterSwitch)
    entity.coordinator = coordinator
    entity._device = coordinator.device  # noqa: SLF001
    entity._attr_name = "Heater"  # noqa: SLF001
    entity._attr_has_entity_name = False  # noqa: SLF001
    entity._attr_translation_key = None  # noqa: SLF001
    entity._is_on = False  # noqa: SLF001
    return entity


def test_breezer_commands_reload_after_waiting_for_same_device_lock() -> None:
    """Test a second full breezer payload keeps fields changed by the first."""
    coordinator = FakeCoordinator()
    max_speed = _max_speed(coordinator)
    heater = _heater(coordinator)

    async def _run() -> None:
        first = asyncio.create_task(max_speed.async_set_native_value(2))
        await coordinator.send_started.wait()

        second = asyncio.create_task(heater.async_turn_on())
        await asyncio.sleep(0)

        coordinator.release_first_send.set()
        await coordinator.second_send_started.wait()
        coordinator.release_second_send.set()

        await first
        await second

    asyncio.run(_run())

    assert coordinator.commands[0]["speed_max_set"] == 2
    assert coordinator.commands[1]["speed_max_set"] == 2
    assert coordinator.commands[1]["heater_mode"] == "heat"
    assert coordinator.device.data.speed_max_set == 2
    assert coordinator.device.data.heater_mode == "heat"
