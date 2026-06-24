"""Tests for Tion climate entities."""

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from custom_components.tion.climate import TionClimate
from custom_components.tion.const import SwingMode, TionDeviceType, ZoneMode
from custom_components.tion.presets import ATTR_SAVED_PRESET, TionPresetController
from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    FAN_AUTO,
    PRESET_NONE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError

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
        # Real per-guid locks mirror the coordinator so tests catch a
        # re-entrant double-acquire or breezer<->zone ordering deadlock.
        self._breezer_locks: dict[str, asyncio.Lock] = {}
        self._zone_locks: dict[str, asyncio.Lock] = {}

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
    entity._preset_apply_lock = asyncio.Lock()  # noqa: SLF001
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


def test_climate_set_auto_preset_applies_fan_auto_and_limits() -> None:
    """Test activating an auto preset switches to Fan Auto and pushes limits."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed_min_set=2,
        speed_max_set=5,
        speed=3,
    )
    breezer_calls: list[bool] = []
    zone_calls: list[bool] = []

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        breezer_calls.append(request_refresh)
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        zone_calls.append(request_refresh)
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("eco"))

    assert entity.preset_mode == "eco"
    assert entity._speed_min_set == 1  # noqa: SLF001
    assert entity._speed_max_set == 2  # noqa: SLF001
    assert entity._mode == ZoneMode.AUTO  # noqa: SLF001
    # Mode switch sends zone without refresh; the limits send carries the refresh.
    assert zone_calls == [False]
    assert breezer_calls == [True]


@pytest.mark.parametrize(
    ("speed", "min_speed", "max_speed", "expected"),
    [
        pytest.param(5, 1, 2, 2, id="clamps_down_to_max"),
        pytest.param(1, 3, 5, 3, id="clamps_up_to_min"),
        pytest.param(2, 1, 4, 2, id="within_limits_unchanged"),
    ],
)
def test_climate_auto_preset_clamps_speed_into_limits(
    speed: int, min_speed: int, max_speed: int, expected: int
) -> None:
    """Test applying an auto preset clamps the pushed speed into [min, max]."""
    entity = _preset_climate(
        FakePidManager(),
        presets={
            "eco": {"type": "auto", "min_speed": min_speed, "max_speed": max_speed}
        },
        speed_min_set=1,
        speed_max_set=6,
        speed=speed,
    )
    pushed: list[int | None] = []

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        pushed.append(entity.speed)
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("eco"))

    assert pushed == [expected]


def test_climate_set_manual_preset_applies_speed() -> None:
    """Test activating a manual preset switches to manual at the target speed."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
        speed=2,
    )
    breezer_calls: list[bool] = []

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        breezer_calls.append(request_refresh)
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("boost"))

    assert entity.preset_mode == "boost"
    assert entity._mode == ZoneMode.MANUAL  # noqa: SLF001
    assert entity.speed == 3
    assert breezer_calls == [True]


def test_climate_set_preset_mode_rejects_unknown() -> None:
    """Test an unknown preset name does not send a command."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
    )
    send_calls = 0

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal send_calls
        send_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("nonexistent"))

    assert send_calls == 0
    assert entity.preset_mode == PRESET_NONE


def test_climate_auto_preset_noop_when_fan_auto_unavailable() -> None:
    """Test an auto preset is not applied when Fan Auto is hidden in the zone."""
    pid_manager = FakePidManager(configured_guids={PID_BREEZER_GUID})
    entity = _preset_climate(
        pid_manager,
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        zone_devices=[_device(BREEZER_GUID), _device(PID_BREEZER_GUID)],
    )
    sends: list[str] = []

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        sends.append("breezer")
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        sends.append("zone")
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("eco"))

    assert entity.preset_mode == PRESET_NONE
    assert sends == []


def test_climate_preset_does_not_change_power() -> None:
    """Test activating a preset does not toggle the breezer power."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
        speed=2,
    )
    entity._is_on = False  # noqa: SLF001

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("boost"))

    assert entity._is_on is False  # noqa: SLF001


def test_climate_restores_preset() -> None:
    """Test the active preset and saved intent are restored from last state."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
    )
    last_state = SimpleNamespace(
        attributes={
            ATTR_PRESET_MODE: "boost",
            ATTR_SAVED_PRESET: {"type": "auto", "min_speed": 1, "max_speed": 3},
        }
    )

    entity._restore_preset(last_state)  # noqa: SLF001

    assert entity.preset_mode == "boost"
    assert entity.extra_state_attributes[ATTR_SAVED_PRESET] == {
        "type": "auto",
        "min_speed": 1,
        "max_speed": 3,
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


def test_climate_set_preset_none_restores_saved_intent() -> None:
    """Test deactivating a preset restores and applies the saved intent."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"boost": {"type": "manual", "speed": 3}},
        speed=2,
    )

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("boost"))
    asyncio.run(entity.async_set_preset_mode(PRESET_NONE))

    assert entity.preset_mode == PRESET_NONE

    assert entity.speed == 2


def test_climate_rolls_back_preset_when_apply_cancelled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test cancelled preset application restores the previous preset state."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"sleep": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed_min_set=1,
        speed_max_set=4,
        speed=4,
    )
    send_calls = 0

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal send_calls
        send_calls += 1
        raise asyncio.CancelledError

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001
    caplog.set_level(logging.DEBUG, logger="custom_components.tion.climate")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(entity.async_set_preset_mode("sleep"))

    assert send_calls == 1
    assert entity.preset_mode == PRESET_NONE
    assert entity.extra_state_attributes[ATTR_SAVED_PRESET] is None
    assert "preset apply cancelled before confirmation" in caplog.text


def test_climate_rolls_back_preset_when_apply_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test failed preset application restores the previous preset state."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"sleep": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed_min_set=1,
        speed_max_set=4,
        speed=4,
    )

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        raise HomeAssistantError("boom")

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001
    caplog.set_level(logging.DEBUG, logger="custom_components.tion.climate")

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_preset_mode("sleep"))

    assert entity.preset_mode == PRESET_NONE
    assert entity.extra_state_attributes[ATTR_SAVED_PRESET] is None
    assert "preset apply failed before confirmation" in caplog.text


def test_climate_serializes_preset_apply_and_retries_after_cancellation() -> None:
    """Test a repeated preset call waits for rollback and sends again."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"sleep": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed_min_set=1,
        speed_max_set=4,
        speed=4,
    )

    async def _run() -> int:
        send_started = asyncio.Event()
        release_send = asyncio.Event()
        send_calls = 0

        async def _send_breezer(*, request_refresh: bool = True) -> bool:
            nonlocal send_calls
            send_calls += 1
            send_started.set()
            await release_send.wait()
            return True

        async def _send_zone(*, request_refresh: bool = True) -> bool:
            return True

        entity._send_breezer = _send_breezer  # noqa: SLF001
        entity._send_zone = _send_zone  # noqa: SLF001

        first = asyncio.create_task(entity.async_set_preset_mode("sleep"))
        await send_started.wait()
        second = asyncio.create_task(entity.async_set_preset_mode("sleep"))
        await asyncio.sleep(0)
        first.cancel()
        release_send.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        return send_calls

    assert asyncio.run(_run()) == 2
    assert entity.preset_mode == "sleep"


def test_climate_coordinator_update_resets_preset_on_manual_change() -> None:
    """Test an external limit change resets an auto preset on a coordinator update."""
    entity = _preset_climate(
        FakePidManager(),
        presets={"eco": {"type": "auto", "min_speed": 1, "max_speed": 2}},
        speed=2,
    )

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        return True

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001
    entity._send_zone = _send_zone  # noqa: SLF001

    asyncio.run(entity.async_set_preset_mode("eco"))
    assert entity.preset_mode == "eco"

    # The cloud now reports limits the user changed manually; reconcile drops it.
    entity._speed_min_set = 2  # noqa: SLF001
    entity._speed_max_set = 5  # noqa: SLF001
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


def test_climate_set_temperature_sends_breezer() -> None:
    """Test setting a new target temperature pushes a breezer command."""
    entity = _setter_climate(t_set=20)
    breezer_calls = 0

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal breezer_calls
        breezer_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 25}))

    assert entity._t_set == 25  # noqa: SLF001
    assert breezer_calls == 1


def test_climate_set_temperature_noop_when_unchanged() -> None:
    """Test setting the current target temperature sends nothing."""
    entity = _setter_climate(t_set=22)
    breezer_calls = 0

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal breezer_calls
        breezer_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22}))

    assert breezer_calls == 0


def test_climate_set_swing_mode_sends_breezer_gate() -> None:
    """Test selecting a swing mode pushes the new gate on a breezer command."""
    entity = _setter_climate(gate=0)
    breezer_calls = 0

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal breezer_calls
        breezer_calls += 1
        return True

    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_swing_mode(SwingMode.SWING_INSIDE))

    assert entity._gate == 1  # noqa: SLF001
    assert breezer_calls == 1


def test_climate_set_hvac_off_sends_zone_and_breezer() -> None:
    """Test turning the breezer off flips the zone to manual and powers down."""
    entity = _setter_climate(is_on=True, mode=ZoneMode.AUTO)
    zone_calls = 0
    breezer_calls = 0

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        nonlocal zone_calls
        zone_calls += 1
        return True

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal breezer_calls
        breezer_calls += 1
        return True

    entity._send_zone = _send_zone  # noqa: SLF001
    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))

    assert entity._is_on is False  # noqa: SLF001
    assert entity._mode == ZoneMode.MANUAL  # noqa: SLF001
    assert zone_calls == 1
    assert breezer_calls == 1


def test_climate_set_hvac_heat_turns_on_from_off() -> None:
    """Test selecting heat from off powers up and enables the heater."""
    entity = _setter_climate(is_on=False)
    breezer_calls = 0

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        return True

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal breezer_calls
        breezer_calls += 1
        return True

    entity._send_zone = _send_zone  # noqa: SLF001
    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))

    assert entity._is_on is True  # noqa: SLF001
    assert entity.heater_enabled is True
    assert breezer_calls == 1


def test_climate_set_fan_mode_manual_sends_zone_and_breezer() -> None:
    """Test a manual fan speed pins the zone to manual and pushes the speed."""
    entity = _setter_climate(mode=ZoneMode.AUTO, speed=1)
    zone_calls = 0
    breezer_calls = 0

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        nonlocal zone_calls
        zone_calls += 1
        return True

    async def _send_breezer(*, request_refresh: bool = True) -> bool:
        nonlocal breezer_calls
        breezer_calls += 1
        return True

    entity._send_zone = _send_zone  # noqa: SLF001
    entity._send_breezer = _send_breezer  # noqa: SLF001

    asyncio.run(entity.async_set_fan_mode("3"))

    assert entity._mode == ZoneMode.MANUAL  # noqa: SLF001
    assert entity.speed == 3
    assert zone_calls == 1
    assert breezer_calls == 1


def test_climate_enter_auto_mode_reloads_zone_before_send() -> None:
    """Test entering auto reloads zone state so it never sends a stale CO2."""
    entity = _climate(FakePidManager())
    entity.coordinator.zone = SimpleNamespace(
        guid="zone-guid",
        name="Zone",
        valid=True,
        mode=SimpleNamespace(
            current=ZoneMode.MANUAL, auto_set=SimpleNamespace(co2=800)
        ),
        devices=[],
    )
    entity.coordinator.last_update_success = True
    entity._mode = None  # noqa: SLF001
    entity._gate = None  # noqa: SLF001
    entity._is_online = True  # noqa: SLF001
    entity._breezer_valid = True  # noqa: SLF001
    entity._zone_valid = True  # noqa: SLF001
    entity.async_write_ha_state = lambda: None
    sent: list[int | None] = []

    async def _send_zone(*, request_refresh: bool = True) -> bool:
        sent.append(entity._target_co2)  # noqa: SLF001
        return True

    entity._send_zone = _send_zone  # noqa: SLF001

    # A concurrent writer already pushed a new target CO2 to the cloud.
    entity.coordinator.zone.mode.auto_set.co2 = 900

    asyncio.run(entity._enter_auto_mode())  # noqa: SLF001

    assert entity._mode == ZoneMode.AUTO  # noqa: SLF001
    assert sent == [900]
