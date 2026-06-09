"""Tests for Tion climate entities."""

import asyncio
from types import SimpleNamespace

from custom_components.tion.climate import TionClimate
from custom_components.tion.const import TionDeviceType

from homeassistant.components.climate import FAN_AUTO

BREEZER_GUID = "breezer-guid"
PID_BREEZER_GUID = "pid-breezer-guid"


class FakePidManager:
    """Fake local PID manager."""

    def __init__(
        self,
        *,
        configured: bool = False,
        configured_guids: set[str] | None = None,
    ) -> None:
        """Initialize fake local PID manager."""
        self.configured_guids = configured_guids or set()
        if configured:
            self.configured_guids.add(BREEZER_GUID)
        self.active_calls: list[tuple[str, bool]] = []

    def is_configured(self, breezer_guid: str) -> bool:
        """Return if fake local PID is configured."""
        return breezer_guid in self.configured_guids

    def start_breezer_pid(self, breezer_guid: str) -> bool:
        """Record local PID start."""
        self.active_calls.append((breezer_guid, True))
        return True

    def stop_breezer_pid(self, breezer_guid: str) -> bool:
        """Record local PID stop."""
        self.active_calls.append((breezer_guid, False))
        return True


class FakeCoordinator:
    """Fake Tion coordinator."""

    def __init__(
        self,
        pid_manager: FakePidManager,
        zone_devices: list[SimpleNamespace] | None = None,
    ) -> None:
        """Initialize fake coordinator."""
        self.pid_manager = pid_manager
        self.zone = SimpleNamespace(devices=zone_devices or [])

    def get_device_zone(self, guid: str) -> SimpleNamespace:
        """Return fake zone for the breezer."""
        return self.zone


def _device(guid: str) -> SimpleNamespace:
    """Return a fake breezer-like zone device."""
    return SimpleNamespace(guid=guid, type=TionDeviceType.BREEZER_4S)


def _climate(
    pid_manager: FakePidManager,
    *,
    breezer_guid: str = BREEZER_GUID,
    zone_devices: list[SimpleNamespace] | None = None,
) -> TionClimate:
    """Return a minimal Tion climate entity for restore tests."""
    entity = TionClimate.__new__(TionClimate)
    entity._breezer_guid = breezer_guid  # noqa: SLF001
    entity._attr_name = "Breezer"  # noqa: SLF001
    entity._manual_fan_modes = ["1", "2", "3"]  # noqa: SLF001
    entity.coordinator = FakeCoordinator(pid_manager, zone_devices)
    return entity


def test_climate_restores_active_local_pid() -> None:
    """Test local PID is restored from previous pid_active attribute."""
    pid_manager = FakePidManager(configured=True)
    entity = _climate(pid_manager)
    last_state = SimpleNamespace(attributes={"pid_active": True})

    entity._restore_local_pid(last_state)  # noqa: SLF001

    assert pid_manager.active_calls == [(BREEZER_GUID, True)]


def test_climate_does_not_restore_from_fan_auto_without_pid_active() -> None:
    """Test MagicAir auto is not restored as local PID."""
    pid_manager = FakePidManager(configured=True)
    entity = _climate(pid_manager)
    last_state = SimpleNamespace(attributes={"fan_mode": FAN_AUTO})

    entity._restore_local_pid(last_state)  # noqa: SLF001

    assert pid_manager.active_calls == []


def test_climate_does_not_restore_unconfigured_local_pid() -> None:
    """Test local PID restore is ignored when PID options are not configured."""
    pid_manager = FakePidManager(configured=False)
    entity = _climate(pid_manager)
    last_state = SimpleNamespace(attributes={"pid_active": True})

    entity._restore_local_pid(last_state)  # noqa: SLF001

    assert pid_manager.active_calls == []


def test_climate_hides_fan_auto_for_non_pid_breezer_in_local_pid_zone() -> None:
    """Test Fan Auto is hidden when another breezer in the zone has local PID."""
    pid_manager = FakePidManager(configured_guids={PID_BREEZER_GUID})
    entity = _climate(
        pid_manager,
        zone_devices=[_device(BREEZER_GUID), _device(PID_BREEZER_GUID)],
    )

    assert FAN_AUTO not in entity.fan_modes
    assert entity.fan_modes == ["1", "2", "3"]


def test_climate_keeps_fan_auto_for_pid_configured_breezer() -> None:
    """Test Fan Auto remains available for the local PID breezer."""
    pid_manager = FakePidManager(configured=True)
    entity = _climate(
        pid_manager,
        zone_devices=[_device(BREEZER_GUID), _device(PID_BREEZER_GUID)],
    )

    assert entity.fan_modes == [FAN_AUTO, "1", "2", "3"]


def test_climate_keeps_fan_auto_for_non_pid_breezer_in_other_zone() -> None:
    """Test Fan Auto is still available in zones without configured local PID."""
    pid_manager = FakePidManager(configured_guids={PID_BREEZER_GUID})
    entity = _climate(pid_manager, zone_devices=[_device(BREEZER_GUID)])

    assert entity.fan_modes == [FAN_AUTO, "1", "2", "3"]


def test_climate_rejects_fan_auto_for_non_pid_breezer_in_local_pid_zone() -> None:
    """Test hidden Fan Auto does not send a cloud auto zone command."""
    pid_manager = FakePidManager(configured_guids={PID_BREEZER_GUID})
    entity = _climate(
        pid_manager,
        zone_devices=[_device(BREEZER_GUID), _device(PID_BREEZER_GUID)],
    )
    send_zone_calls = 0

    async def _send_zone() -> None:
        nonlocal send_zone_calls
        send_zone_calls += 1

    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_fan_mode(FAN_AUTO))

    assert send_zone_calls == 0
    assert pid_manager.active_calls == []
