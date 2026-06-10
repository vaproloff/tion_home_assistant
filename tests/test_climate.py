"""Tests for Tion climate entities."""

import asyncio
from types import SimpleNamespace

from custom_components.tion.climate import TionClimate
from custom_components.tion.const import TionDeviceType

from homeassistant.components.climate import ATTR_PRESET_MODE, FAN_AUTO, PRESET_NONE

from custom_components.tion.presets import (
    ATTR_SAVED_MAX_SPEED,
    ATTR_SAVED_MIN_SPEED,
    TionPresetController,
)

BREEZER_GUID = "breezer-guid"
PID_BREEZER_GUID = "pid-breezer-guid"


async def _noop_send() -> bool:
    """No-op breezer send for tests."""
    return True


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

    def extra_state_attributes(self, breezer_guid: str) -> dict:
        """Return fake PID attributes."""
        return {}


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


def _preset_climate(
    pid_manager: FakePidManager,
    *,
    presets: dict[str, dict[str, int]] | None = None,
    speed_min_set: int = 1,
    speed_max_set: int = 3,
) -> TionClimate:
    """Return a minimal climate entity wired with a preset controller."""
    entity = _climate(pid_manager)
    entity._presets = TionPresetController(presets or {})  # noqa: SLF001
    entity._speed_min_set = speed_min_set  # noqa: SLF001
    entity._speed_max_set = speed_max_set  # noqa: SLF001
    entity._mode = None  # noqa: SLF001
    entity._zone_valid = False  # noqa: SLF001
    entity._speed = speed_min_set  # noqa: SLF001
    entity._heater_power = None  # noqa: SLF001
    entity.async_write_ha_state = lambda: None
    entity._load_zone = lambda: None  # noqa: SLF001
    entity._load_breezer = lambda: None  # noqa: SLF001
    return entity


def test_climate_set_preset_mode_sends_limits() -> None:
    """Test activating a preset pushes the preset min/max speed to the cloud."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"min_speed": 4, "max_speed": 6}},
    )
    send_calls = 0

    async def _send_breezer() -> bool:
        nonlocal send_calls
        send_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("boost"))

    assert send_calls == 1
    assert entity._speed_min_set == 4  # noqa: SLF001
    assert entity._speed_max_set == 6  # noqa: SLF001
    assert entity.preset_mode == "boost"


def test_climate_set_preset_mode_rejects_unknown() -> None:
    """Test an unknown preset name does not send a command."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"min_speed": 4, "max_speed": 6}},
    )
    send_calls = 0

    async def _send_breezer() -> bool:
        nonlocal send_calls
        send_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("nonexistent"))

    assert send_calls == 0
    assert entity.preset_mode == PRESET_NONE


def test_climate_restores_preset() -> None:
    """Test the active preset and saved limits are restored from last state."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"min_speed": 4, "max_speed": 6}},
    )
    last_state = SimpleNamespace(
        attributes={
            ATTR_PRESET_MODE: "boost",
            ATTR_SAVED_MIN_SPEED: 1,
            ATTR_SAVED_MAX_SPEED: 3,
        }
    )

    entity._restore_preset(last_state)  # noqa: SLF001

    assert entity.preset_mode == "boost"
    assert entity.extra_state_attributes[ATTR_SAVED_MIN_SPEED] == 1


def test_climate_restore_ignores_none_preset() -> None:
    """Test restore does nothing when the previous preset was PRESET_NONE."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"min_speed": 4, "max_speed": 6}},
    )
    last_state = SimpleNamespace(attributes={ATTR_PRESET_MODE: PRESET_NONE})

    entity._restore_preset(last_state)  # noqa: SLF001

    assert entity.preset_mode == PRESET_NONE


def test_climate_preset_modes_none_without_presets() -> None:
    """Test preset_modes is None when no presets are configured."""
    entity = _preset_climate(FakePidManager(), presets={})

    assert entity.preset_modes is None
    assert entity.preset_mode is None


def test_climate_set_preset_none_restores_saved_limits() -> None:
    """Test deactivating a preset restores the saved limits and pushes them."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"min_speed": 4, "max_speed": 6}},
        speed_min_set=1,
        speed_max_set=3,
    )
    send_calls = 0

    async def _send_breezer() -> bool:
        nonlocal send_calls
        send_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("boost"))
    # Cloud confirms the applied limits so the pending gate clears.
    entity._presets.reconcile(4, 6)  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode(PRESET_NONE))

    assert send_calls == 2
    assert entity._speed_min_set == 1  # noqa: SLF001
    assert entity._speed_max_set == 3  # noqa: SLF001
    assert entity.preset_mode == PRESET_NONE


def test_climate_coordinator_update_resets_preset_on_manual_change() -> None:
    """Test an external limit change resets the preset on a coordinator update."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"min_speed": 4, "max_speed": 6}},
    )
    entity._send_breezer = _noop_send  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("boost"))
    # Confirm the applied limits so the pending gate clears.
    entity._presets.reconcile(4, 6)  # noqa: SLF001

    # The cloud now reports limits the user changed manually (via the number
    # entity or the Tion app); _handle_coordinator_update must drop the preset.
    entity._speed_min_set = 2  # noqa: SLF001
    entity._speed_max_set = 5  # noqa: SLF001
    entity._handle_coordinator_update()  # noqa: SLF001

    assert entity.preset_mode == PRESET_NONE
