"""Tests for Tion switch entities."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tion.const import Heater, TionDeviceType, ZoneMode
from custom_components.tion.switch import (
    TionAutoModeSwitch,
    TionBacklightSwitch,
    TionBreezerHeaterSwitch,
    TionBreezerSoundSwitch,
)

DEVICE_GUID = "device-guid"
ZONE_GUID = "zone-guid"


class FakeReconciler:
    """Fake reconciler recording desired-state writes."""

    def __init__(self) -> None:
        """Initialize empty desired overlays."""
        self.breezer: dict[str, dict[str, Any]] = {}
        self.zone: dict[str, dict[str, Any]] = {}

    def set_breezer(self, guid: str, fields: dict[str, Any]) -> None:
        """Record a breezer desired write."""
        self.breezer.setdefault(guid, {}).update(fields)

    def set_zone(self, guid: str, fields: dict[str, Any]) -> None:
        """Record a zone desired write."""
        self.zone.setdefault(guid, {}).update(fields)

    def reconcile(self, data: Any) -> None:
        """No-op reconcile."""


class FakeCoordinator:
    """Fake Tion coordinator for switch tests."""

    def __init__(
        self,
        device: SimpleNamespace,
        zone: SimpleNamespace | None = None,
    ) -> None:
        """Initialize fake coordinator."""
        self.reconciler = FakeReconciler()
        self.last_update_success = True
        self.data = SimpleNamespace()
        self._device = device
        self._zone = zone
        self.settings_calls: list[tuple[str, dict[str, int]]] = []
        self._settings_locks: dict[str, asyncio.Lock] = {}

    async def async_request_refresh(self) -> None:
        """Record a refresh request (no-op)."""

    def get_device(self, guid: str) -> SimpleNamespace:
        """Return the fake device."""
        return self._device

    def get_device_zone(self, guid: str) -> SimpleNamespace | None:
        """Return the fake zone."""
        return self._zone

    async def async_send_settings(self, *, guid: str, data: dict[str, int]) -> None:
        """Record a settings command."""
        self.settings_calls.append((guid, data))

    @asynccontextmanager
    async def async_settings_command(self, guid: str):
        """Serialize settings writes for one device (real lock)."""
        async with self._settings_locks.setdefault(guid, asyncio.Lock()):
            yield


def _device(
    *,
    device_type: TionDeviceType = TionDeviceType.BREEZER_4S,
    heater_mode: str = Heater.OFF,
    heater_enabled: bool | None = None,
    backlight: int = 0,
    sound_is_on: int = 0,
) -> SimpleNamespace:
    """Return a fake Tion device."""
    return SimpleNamespace(
        guid=DEVICE_GUID,
        name="Device",
        type=device_type,
        is_online=True,
        valid=True,
        data=SimpleNamespace(
            heater_mode=heater_mode,
            heater_enabled=heater_enabled,
            backlight=backlight,
            sound_is_on=sound_is_on,
        ),
    )


def _zone(mode: ZoneMode = ZoneMode.MANUAL) -> SimpleNamespace:
    """Return a fake zone."""
    return SimpleNamespace(
        guid=ZONE_GUID,
        mode=SimpleNamespace(current=mode, auto_set=SimpleNamespace(co2=800)),
        devices=[],
    )


def _build(switch_cls: type, coordinator: FakeCoordinator) -> Any:
    """Return a switch of the given class bound to the coordinator."""
    switch = switch_cls.__new__(switch_cls)
    switch.coordinator = coordinator
    switch._device = coordinator._device  # noqa: SLF001
    switch._attr_name = switch_cls.__name__  # noqa: SLF001
    switch._is_on = None  # noqa: SLF001
    switch.async_write_ha_state = lambda: None
    return switch


def test_auto_mode_on_writes_zone_auto() -> None:
    """Test enabling auto mode writes the zone AUTO desired."""
    coordinator = FakeCoordinator(
        _device(device_type=TionDeviceType.MAGIC_AIR), _zone()
    )
    switch = _build(TionAutoModeSwitch, coordinator)

    asyncio.run(switch.async_turn_on())

    assert coordinator.reconciler.zone[ZONE_GUID] == {"mode": ZoneMode.AUTO}


def test_auto_mode_off_writes_zone_manual() -> None:
    """Test disabling auto mode writes the zone MANUAL desired."""
    coordinator = FakeCoordinator(
        _device(device_type=TionDeviceType.MAGIC_AIR), _zone(ZoneMode.AUTO)
    )
    switch = _build(TionAutoModeSwitch, coordinator)

    asyncio.run(switch.async_turn_off())

    assert coordinator.reconciler.zone[ZONE_GUID] == {"mode": ZoneMode.MANUAL}


def test_heater_4s_on_writes_heater_mode() -> None:
    """Test enabling the 4S heater writes heater_mode ON desired."""
    coordinator = FakeCoordinator(_device(device_type=TionDeviceType.BREEZER_4S))
    switch = _build(TionBreezerHeaterSwitch, coordinator)

    asyncio.run(switch.async_turn_on())

    assert coordinator.reconciler.breezer[DEVICE_GUID] == {"heater_mode": Heater.ON}


def test_heater_4s_off_writes_heater_mode_off() -> None:
    """Test disabling the 4S heater writes heater_mode OFF desired."""
    coordinator = FakeCoordinator(
        _device(device_type=TionDeviceType.BREEZER_4S, heater_mode=Heater.ON)
    )
    switch = _build(TionBreezerHeaterSwitch, coordinator)

    asyncio.run(switch.async_turn_off())

    assert coordinator.reconciler.breezer[DEVICE_GUID] == {"heater_mode": Heater.OFF}


def test_heater_non_4s_on_writes_heater_enabled() -> None:
    """Test enabling a non-4S heater writes heater_enabled desired."""
    coordinator = FakeCoordinator(
        _device(device_type=TionDeviceType.BREEZER_3S, heater_enabled=False)
    )
    switch = _build(TionBreezerHeaterSwitch, coordinator)

    asyncio.run(switch.async_turn_on())

    assert coordinator.reconciler.breezer[DEVICE_GUID] == {"heater_enabled": True}


@pytest.mark.parametrize(
    ("switch_cls", "field"),
    [
        (TionBacklightSwitch, "backlight"),
        (TionBreezerSoundSwitch, "sound"),
    ],
    ids=["backlight", "sound"],
)
def test_settings_switch_sends_settings(switch_cls: type, field: str) -> None:
    """Test backlight/sound switches write through the settings endpoint."""
    coordinator = FakeCoordinator(_device())
    switch = _build(switch_cls, coordinator)

    asyncio.run(switch.async_turn_on())

    assert coordinator.settings_calls == [(DEVICE_GUID, {field: 1})]
    assert coordinator.reconciler.breezer == {}
    assert coordinator.reconciler.zone == {}


def test_settings_commands_serialize_per_device() -> None:
    """Test two settings writes on one device run one at a time under the lock."""
    coordinator = FakeCoordinator(_device())
    backlight = _build(TionBacklightSwitch, coordinator)
    sound = _build(TionBreezerSoundSwitch, coordinator)
    order: list[str] = []
    first_in = asyncio.Event()
    release_first = asyncio.Event()

    async def _send_settings(*, guid: str, data: dict[str, int]) -> None:
        order.append(next(iter(data)))
        if len(order) == 1:
            first_in.set()
            await release_first.wait()

    coordinator.async_send_settings = _send_settings  # type: ignore[method-assign]

    async def _run() -> None:
        first = asyncio.create_task(backlight.async_turn_on())
        await first_in.wait()
        second = asyncio.create_task(sound.async_turn_on())
        await asyncio.sleep(0)
        # The second write must still be blocked on the settings lock.
        assert order == ["backlight"]
        release_first.set()
        await first
        await second

    asyncio.run(_run())

    assert order == ["backlight", "sound"]
