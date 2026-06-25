"""Tests for Tion climate entities."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tion.climate import (
    ATTR_DESIRED_BREEZER,
    ATTR_DESIRED_ZONE,
    TionClimate,
)
from custom_components.tion.const import Heater, SwingMode, TionDeviceType, ZoneMode
from custom_components.tion.presets import (
    ATTR_SAVED_PRESET,
    PresetBaseline,
    TionPresetController,
)
from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    FAN_AUTO,
    PRESET_NONE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE

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
        self.active_guids: set[str] = set()

    def is_configured(self, breezer_guid: str) -> bool:
        """Return if fake local PID is configured."""
        return breezer_guid in self.configured_guids

    def is_active(self, breezer_guid: str) -> bool:
        """Return if fake local PID is active."""
        return breezer_guid in self.active_guids

    def start_breezer_pid(self, breezer_guid: str) -> bool:
        """Record local PID start."""
        self.active_calls.append((breezer_guid, True))
        self.active_guids.add(breezer_guid)
        return True

    def stop_breezer_pid(self, breezer_guid: str) -> bool:
        """Record local PID stop."""
        self.active_calls.append((breezer_guid, False))
        self.active_guids.discard(breezer_guid)
        return True

    def extra_state_attributes(self, breezer_guid: str) -> dict:
        """Return fake PID attributes."""
        return {}


class FakeReconciler:
    """Fake reconciler recording desired-state writes for preset tests."""

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

    def current_breezer(self, guid: str) -> dict[str, Any]:
        """Return a copy of the breezer's desired overlay."""
        return dict(self.breezer.get(guid, {}))

    def current_zone(self, guid: str) -> dict[str, Any]:
        """Return a copy of the zone's desired overlay."""
        return dict(self.zone.get(guid, {}))

    def holds(self, guid: str, fields: Any) -> bool:
        """Return whether all fields are still desired for the breezer."""
        desired = self.breezer.get(guid, {})
        return all(field in desired for field in fields)

    def release(self, guid: str, fields: Any) -> None:
        """Drop fields from the breezer's desired overlay."""
        desired = self.breezer.get(guid)
        if desired is not None:
            for field in fields:
                desired.pop(field, None)

    def reconcile(self, data: Any) -> None:
        """No-op reconcile for preset tests."""


class FakeCoordinator:
    """Fake Tion coordinator."""

    def __init__(
        self,
        pid_manager: FakePidManager,
        zone_devices: list[SimpleNamespace] | None = None,
    ) -> None:
        """Initialize fake coordinator."""
        self.pid_manager = pid_manager
        self.reconciler = FakeReconciler()
        self.data: SimpleNamespace | None = None
        self.zone = SimpleNamespace(devices=zone_devices or [])
        # Real per-guid locks mirror the coordinator so tests catch a
        # re-entrant double-acquire or breezer<->zone ordering deadlock.
        self._breezer_locks: dict[str, asyncio.Lock] = {}
        self._zone_locks: dict[str, asyncio.Lock] = {}

    async def async_request_refresh(self) -> None:
        """Record a refresh request (no-op)."""

    def get_device_zone(self, guid: str) -> SimpleNamespace:
        """Return fake zone for the breezer."""
        return self.zone

    def zone_mode_command_key_for_device(self, guid: str) -> str:
        """Return the zone command lock key for a device guid."""
        if (zone := self.get_device_zone(guid)) is not None and (
            zone_guid := getattr(zone, "guid", None)
        ) is not None:
            return zone_guid

        return guid

    @asynccontextmanager
    async def async_breezer_mode_command(self, guid: str):
        """Provide a fake breezer command critical section."""
        assert guid
        async with self._breezer_locks.setdefault(guid, asyncio.Lock()):
            yield

    @asynccontextmanager
    async def async_zone_mode_command(self, guid: str):
        """Provide a fake zone command critical section."""
        assert guid
        async with self._zone_locks.setdefault(guid, asyncio.Lock()):
            yield


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
    presets: dict[str, dict[str, int | str]] | None = None,
    speed_min_set: int = 1,
    speed_max_set: int = 3,
    speed: int = 1,
    zone_devices: list[SimpleNamespace] | None = None,
) -> TionClimate:
    """Return a minimal climate entity wired with a preset controller."""
    entity = _climate(pid_manager, zone_devices=zone_devices)
    entity._presets = TionPresetController(presets or {})  # noqa: SLF001
    entity._zone_guid = "zone-guid"  # noqa: SLF001
    entity._speed_min_set = speed_min_set  # noqa: SLF001
    entity._speed_max_set = speed_max_set  # noqa: SLF001
    entity._mode = None  # noqa: SLF001
    entity._zone_valid = False  # noqa: SLF001
    entity._speed = speed  # noqa: SLF001
    entity._heater_power = None  # noqa: SLF001
    entity._gate = None  # noqa: SLF001
    entity.async_write_ha_state = lambda: None
    entity._load_zone = lambda: None  # noqa: SLF001
    entity._load_breezer = lambda: None  # noqa: SLF001
    return entity


def test_climate_set_auto_preset_writes_limits_and_enters_cloud_auto() -> None:
    """Test an auto preset writes the limits and switches the zone to cloud auto."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed_min_set=2,
        speed_max_set=5,
        speed=3,
    )

    asyncio.run(entity.async_set_preset_mode("eco"))

    reconciler = entity.coordinator.reconciler
    assert entity.preset_mode == "eco"
    assert reconciler.breezer[BREEZER_GUID] == {"speed_min_set": 1, "speed_max_set": 2}
    assert reconciler.zone["zone-guid"] == {"mode": ZoneMode.AUTO}


def test_climate_set_auto_preset_arms_pid_when_configured() -> None:
    """Test an auto preset arms local PID instead of cloud auto when configured."""
    pid_manager = FakePidManager(configured=True)
    entity = _preset_climate(
        pid_manager,
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
    )

    asyncio.run(entity.async_set_preset_mode("eco"))

    assert entity.preset_mode == "eco"
    assert (BREEZER_GUID, True) in pid_manager.active_calls
    assert entity.coordinator.reconciler.zone == {}  # PID drives, no cloud auto


def test_climate_set_manual_preset_writes_speed_and_manual() -> None:
    """Test a manual preset writes on/speed, manual zone, and disarms PID."""
    pid_manager = FakePidManager(configured=True)
    pid_manager.active_guids.add(BREEZER_GUID)
    entity = _preset_climate(
        pid_manager,
        presets={"boost": {"type": "manual", "speed": 3}},
        speed=2,
    )

    asyncio.run(entity.async_set_preset_mode("boost"))

    reconciler = entity.coordinator.reconciler
    assert entity.preset_mode == "boost"
    assert reconciler.breezer[BREEZER_GUID] == {"is_on": True, "speed": 3}
    assert reconciler.zone["zone-guid"] == {"mode": ZoneMode.MANUAL}
    assert (BREEZER_GUID, False) in pid_manager.active_calls


def test_climate_set_preset_mode_rejects_unknown() -> None:
    """Test an unknown preset name writes nothing."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
    )

    asyncio.run(entity.async_set_preset_mode("nonexistent"))

    assert entity.preset_mode == PRESET_NONE
    assert entity.coordinator.reconciler.breezer == {}


def test_climate_auto_preset_noop_when_fan_auto_unavailable() -> None:
    """Test an auto preset is not applied when Fan Auto is hidden in the zone."""
    pid_manager = FakePidManager(configured_guids={PID_BREEZER_GUID})
    entity = _preset_climate(
        pid_manager,
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        zone_devices=[_device(BREEZER_GUID), _device(PID_BREEZER_GUID)],
    )

    asyncio.run(entity.async_set_preset_mode("eco"))

    assert entity.preset_mode == PRESET_NONE
    assert entity.coordinator.reconciler.breezer == {}


def test_climate_preset_double_apply_keeps_original_baseline() -> None:
    """Restart-mode double-apply of an auto preset must not pollute the baseline.

    The user's bug: a cancelled-then-retried preset apply captured the baseline
    from already-changed state. With a synchronous apply the baseline (the
    desired overlay before the preset) is fixed before any await, so cancelling
    mid-flight and re-firing cannot pollute it.
    """
    pid_manager = FakePidManager(configured=True)
    pid_manager.active_guids.add(BREEZER_GUID)  # fan_mode == FAN_AUTO -> was_auto
    entity = _preset_climate(
        pid_manager,
        presets={"sleep": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed_min_set=1,
        speed_max_set=4,
        speed=4,
    )
    # The "none" desired overlay before the preset: auto limits 1..4.
    entity.coordinator.reconciler.set_breezer(
        BREEZER_GUID, {"speed_min_set": 1, "speed_max_set": 4}
    )
    release = asyncio.Event()

    async def _blocking_refresh() -> None:
        await release.wait()

    entity.coordinator.async_request_refresh = _blocking_refresh  # type: ignore[method-assign]

    async def _run() -> None:
        first = asyncio.create_task(entity.async_set_preset_mode("sleep"))
        await asyncio.sleep(0)  # first runs the sync apply, then blocks on refresh
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await entity.async_set_preset_mode("sleep")  # restart re-fire short-circuits

    asyncio.run(_run())

    assert entity.preset_mode == "sleep"
    saved = entity._presets.saved  # noqa: SLF001
    assert saved is not None
    assert saved.overrides == {"speed_min_set": 1, "speed_max_set": 4}


def test_climate_restores_preset() -> None:
    """Test the active preset and saved intent are restored from last state."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
    )
    last_state = SimpleNamespace(
        attributes={
            ATTR_PRESET_MODE: "boost",
            ATTR_SAVED_PRESET: {
                "overrides": {"speed_min_set": 1, "speed_max_set": 3},
                "was_auto": True,
            },
        }
    )

    entity._restore_preset(last_state)  # noqa: SLF001

    assert entity.preset_mode == "boost"
    assert entity.extra_state_attributes[ATTR_SAVED_PRESET] == {
        "overrides": {"speed_min_set": 1, "speed_max_set": 3},
        "was_auto": True,
    }


def test_climate_restore_ignores_none_preset() -> None:
    """Test restore does nothing when the previous preset was PRESET_NONE."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
    )
    last_state = SimpleNamespace(attributes={ATTR_PRESET_MODE: PRESET_NONE})

    entity._restore_preset(last_state)  # noqa: SLF001

    assert entity.preset_mode == PRESET_NONE


def test_climate_preset_modes_none_without_presets() -> None:
    """Test preset_modes is None when no presets are configured."""
    entity = _preset_climate(FakePidManager(), presets={})

    assert entity.preset_modes is None
    assert entity.preset_mode is None


def test_climate_set_preset_none_restores_baseline() -> None:
    """Test returning to none restores the saved baseline overlay and clears it."""
    pid_manager = FakePidManager(configured=True)
    pid_manager.active_guids.add(BREEZER_GUID)
    entity = _preset_climate(
        pid_manager,
        presets={"sleep": {"type": "auto", "min_speed": 1, "max_speed": 2}},
    )
    reconciler = entity.coordinator.reconciler
    reconciler.set_breezer(BREEZER_GUID, {"speed_min_set": 1, "speed_max_set": 4})

    asyncio.run(entity.async_set_preset_mode("sleep"))
    assert entity.preset_mode == "sleep"
    assert reconciler.breezer[BREEZER_GUID] == {"speed_min_set": 1, "speed_max_set": 2}

    asyncio.run(entity.async_set_preset_mode(PRESET_NONE))

    assert entity.preset_mode == PRESET_NONE
    assert reconciler.breezer[BREEZER_GUID] == {"speed_min_set": 1, "speed_max_set": 4}


def test_climate_coordinator_update_releases_preset_on_external_change() -> None:
    """Test the preset clears when the reconciler drops its managed fields."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed=2,
    )

    asyncio.run(entity.async_set_preset_mode("eco"))
    assert entity.preset_mode == "eco"

    # An external change made the reconciler release the managed fields.
    entity.coordinator.reconciler.release(
        BREEZER_GUID, ["speed_min_set", "speed_max_set"]
    )
    entity._handle_coordinator_update()  # noqa: SLF001

    assert entity.preset_mode == PRESET_NONE


def _setter_climate(
    *,
    mode: ZoneMode | None = ZoneMode.MANUAL,
    speed: int = 1,
    gate: int = 0,
    is_on: bool = True,
    t_set: int = 20,
    heater_mode: str = "maintenance",
) -> TionClimate:
    """Return a climate entity wired for direct setter tests."""
    entity = _climate(FakePidManager())
    entity._presets = TionPresetController({})  # noqa: SLF001
    entity._zone_guid = "zone-guid"  # noqa: SLF001
    entity._type = TionDeviceType.BREEZER_4S  # noqa: SLF001
    entity._breezer_valid = True  # noqa: SLF001
    entity._zone_valid = True  # noqa: SLF001
    entity._is_on = is_on  # noqa: SLF001
    entity._mode = mode  # noqa: SLF001
    entity._speed = speed  # noqa: SLF001
    entity._gate = gate  # noqa: SLF001
    entity._t_set = t_set  # noqa: SLF001
    entity._heater_mode = heater_mode  # noqa: SLF001
    entity._heater_enabled = None  # noqa: SLF001
    entity._speed_min_set = 1  # noqa: SLF001
    entity._speed_max_set = 4  # noqa: SLF001
    entity._hvac_modes = [  # noqa: SLF001
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
    ]
    entity._swing_modes = [  # noqa: SLF001
        SwingMode.SWING_OUTSIDE,
        SwingMode.SWING_INSIDE,
    ]
    entity._attr_supported_features = (  # noqa: SLF001
        ClimateEntityFeature.TARGET_TEMPERATURE
    )
    entity.async_write_ha_state = lambda: None
    entity._load_breezer = lambda *a, **k: None  # noqa: SLF001
    entity._load_zone = lambda *a, **k: None  # noqa: SLF001
    return entity


def test_climate_set_temperature_writes_desired() -> None:
    """Test setting a new target temperature writes the breezer desired t_set."""
    entity = _setter_climate(t_set=20)

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 25}))

    assert entity.coordinator.reconciler.breezer[BREEZER_GUID] == {"t_set": 25}


def test_climate_set_temperature_noop_when_unchanged() -> None:
    """Test setting the current target temperature writes nothing."""
    entity = _setter_climate(t_set=22)

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22}))

    assert entity.coordinator.reconciler.breezer == {}


def test_climate_set_swing_mode_writes_desired_gate() -> None:
    """Test selecting a swing mode writes the new gate into breezer desired."""
    entity = _setter_climate(gate=0)

    asyncio.run(entity.async_set_swing_mode(SwingMode.SWING_INSIDE))

    assert entity.coordinator.reconciler.breezer[BREEZER_GUID] == {"gate": 1}


def test_climate_set_hvac_off_writes_desired_zone_and_breezer() -> None:
    """Test turning the breezer off writes manual zone + is_on False and stops PID."""
    entity = _setter_climate(is_on=True, mode=ZoneMode.AUTO)
    entity.coordinator.pid_manager.active_guids.add(BREEZER_GUID)

    asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))

    reconciler = entity.coordinator.reconciler
    assert reconciler.breezer[BREEZER_GUID] == {"is_on": False}
    assert reconciler.zone["zone-guid"] == {"mode": ZoneMode.MANUAL}
    assert (BREEZER_GUID, False) in entity.coordinator.pid_manager.active_calls


def test_climate_set_hvac_heat_turns_on_from_off() -> None:
    """Test selecting heat from off writes is_on True and enables the heater."""
    entity = _setter_climate(is_on=False)

    asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))

    assert entity.coordinator.reconciler.breezer[BREEZER_GUID] == {
        "is_on": True,
        "heater_mode": Heater.ON,
    }


def test_climate_set_fan_mode_manual_writes_desired_zone_and_speed() -> None:
    """Test a manual fan speed writes manual zone + speed and stops PID."""
    entity = _setter_climate(mode=ZoneMode.AUTO, speed=1)
    entity.coordinator.pid_manager.active_guids.add(BREEZER_GUID)

    asyncio.run(entity.async_set_fan_mode("3"))

    reconciler = entity.coordinator.reconciler
    assert reconciler.zone["zone-guid"] == {"mode": ZoneMode.MANUAL}
    assert reconciler.breezer[BREEZER_GUID] == {"speed": 3}
    assert (BREEZER_GUID, False) in entity.coordinator.pid_manager.active_calls


def test_climate_set_fan_mode_manual_deactivates_active_preset() -> None:
    """Test a manual fan speed releases preset fields and clears the preset."""
    entity = _setter_climate(mode=ZoneMode.AUTO, speed=1)
    entity._presets = TionPresetController(  # noqa: SLF001
        {"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}}
    )
    entity.coordinator.reconciler.set_breezer(
        BREEZER_GUID, {"speed_min_set": 1, "speed_max_set": 2}
    )
    entity._presets.activate(  # noqa: SLF001
        "eco", PresetBaseline(overrides={}, was_auto=True)
    )

    asyncio.run(entity.async_set_fan_mode("3"))

    assert entity.preset_mode == PRESET_NONE
    assert entity.coordinator.reconciler.breezer[BREEZER_GUID] == {"speed": 3}


def test_climate_set_hvac_off_deactivates_active_preset() -> None:
    """Test turning off releases preset fields and clears the preset."""
    entity = _setter_climate(is_on=True, mode=ZoneMode.AUTO)
    entity._presets = TionPresetController(  # noqa: SLF001
        {"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}}
    )
    entity.coordinator.reconciler.set_breezer(
        BREEZER_GUID, {"speed_min_set": 1, "speed_max_set": 2}
    )
    entity._presets.activate(  # noqa: SLF001
        "eco", PresetBaseline(overrides={}, was_auto=True)
    )

    asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))

    assert entity.preset_mode == PRESET_NONE
    assert entity.coordinator.reconciler.breezer[BREEZER_GUID] == {"is_on": False}


def test_climate_enter_auto_mode_writes_cloud_auto() -> None:
    """Test entering auto without local PID writes the cloud-auto zone desired."""
    entity = _setter_climate(mode=ZoneMode.MANUAL)

    asyncio.run(entity._enter_auto_mode())  # noqa: SLF001

    assert entity.coordinator.reconciler.zone["zone-guid"] == {"mode": ZoneMode.AUTO}


def test_climate_enter_auto_mode_arms_pid_when_configured() -> None:
    """Test entering auto with local PID configured arms PID and writes no zone."""
    entity = _setter_climate(mode=ZoneMode.MANUAL)
    entity.coordinator.pid_manager.configured_guids.add(BREEZER_GUID)

    asyncio.run(entity._enter_auto_mode())  # noqa: SLF001

    assert (BREEZER_GUID, True) in entity.coordinator.pid_manager.active_calls
    assert entity.coordinator.reconciler.zone == {}


def test_climate_apply_auto_limits_writes_desired_and_enters_auto() -> None:
    """Test applying auto limits writes the limits and enters cloud auto."""
    entity = _setter_climate(mode=ZoneMode.MANUAL)

    asyncio.run(entity.async_apply_auto_limits(1, 4))

    reconciler = entity.coordinator.reconciler
    assert reconciler.breezer[BREEZER_GUID] == {"speed_min_set": 1, "speed_max_set": 4}
    assert reconciler.zone["zone-guid"] == {"mode": ZoneMode.AUTO}


def test_climate_rejects_hidden_fan_auto_writes_nothing() -> None:
    """Test selecting a hidden Fan Auto writes no zone desired and arms no PID."""
    pid_manager = FakePidManager(configured_guids={PID_BREEZER_GUID})
    entity = _setter_climate(mode=ZoneMode.MANUAL)
    entity.coordinator.pid_manager = pid_manager
    entity.coordinator.zone = SimpleNamespace(
        devices=[_device(BREEZER_GUID), _device(PID_BREEZER_GUID)]
    )

    asyncio.run(entity.async_set_fan_mode(FAN_AUTO))

    assert entity.coordinator.reconciler.zone == {}
    assert pid_manager.active_calls == []


def test_climate_persists_desired_overlays() -> None:
    """Test the desired breezer/zone overlays are exposed for state restore."""
    entity = _preset_climate(FakePidManager(), presets={})
    entity.coordinator.reconciler.set_breezer(BREEZER_GUID, {"speed": 3})
    entity.coordinator.reconciler.set_zone("zone-guid", {"mode": ZoneMode.MANUAL})

    attrs = entity.extra_state_attributes

    assert attrs[ATTR_DESIRED_BREEZER] == {"speed": 3}
    assert attrs[ATTR_DESIRED_ZONE] == {"mode": ZoneMode.MANUAL}


def test_climate_omits_empty_desired_overlays() -> None:
    """Test empty desired overlays are not written into the state attributes."""
    entity = _preset_climate(FakePidManager(), presets={})

    attrs = entity.extra_state_attributes

    assert ATTR_DESIRED_BREEZER not in attrs
    assert ATTR_DESIRED_ZONE not in attrs


def test_climate_restores_desired_overlays() -> None:
    """Test the desired overlays are rehydrated into the reconciler on restart."""
    entity = _preset_climate(FakePidManager(), presets={})
    last_state = SimpleNamespace(
        attributes={
            ATTR_DESIRED_BREEZER: {"speed": 3},
            ATTR_DESIRED_ZONE: {"mode": ZoneMode.MANUAL},
        }
    )

    entity._restore_desired(last_state)  # noqa: SLF001

    assert entity.coordinator.reconciler.breezer[BREEZER_GUID] == {"speed": 3}
    assert entity.coordinator.reconciler.zone["zone-guid"] == {"mode": ZoneMode.MANUAL}
