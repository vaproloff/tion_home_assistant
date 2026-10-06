"""Tests for local PID control of Tion breezers."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from ha_tests.common import MockConfigEntry, async_fire_time_changed
import pytest

from custom_components.tion.api import (
    Breezer,
    DeviceCommand,
    TionAccount,
    TionConnectionError,
    view,
)
from custom_components.tion.const import (
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_MAX_SPEED,
    CONF_PID_MIN_SPEED,
    CONF_PID_TARGET_CO2,
    PidStatus,
)
from custom_components.tion.pid import PidController
from custom_components.tion.pid_manager import TionPidManager
from homeassistant.core import HomeAssistant

from .api.payloads import PROFILE_4S  # noqa: TID251
from .common import PID_SENSOR as SENSOR, pid_options, setup_entry  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_AUTO,
    BEDROOM_ID,
    BREEZER_3S,
    BREEZER_4S,
    MAGICAIR,
    FakeAuth,
    FakeTionCloud,
    default_account,
    dps,
    replace_device,
    set_room_auto,
    set_values,
)


@pytest.fixture
def options() -> dict[str, Any]:
    """Set up PID on the 4S."""
    return pid_options()


@pytest.fixture
async def manager(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
) -> TionPidManager:
    """The PID manager of an entry loaded without platforms."""
    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, config_entry, cloud, auth)
    return config_entry.runtime_data.pid


def _breezer(cloud: FakeTionCloud) -> Breezer:
    device = cloud.account.device(BREEZER_4S)
    assert device is not None
    breezer = view(device)
    assert isinstance(breezer, Breezer)
    return breezer


def _commands(cloud: FakeTionCloud) -> list[DeviceCommand]:
    return [call for kind, call in cloud.calls if kind == "command"]


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_start_steps_now(
    hass: HomeAssistant, manager: TionPidManager, cloud: FakeTionCloud
) -> None:
    """Starting PID sets the speed at once."""
    hass.states.async_set(SENSOR, "1200")

    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    assert manager.is_active(BREEZER_4S)
    assert manager.status(BREEZER_4S) is PidStatus.RUNNING
    assert _breezer(cloud).speed == 6


async def test_steps_every_pid_interval(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    manager: TionPidManager,
    cloud: FakeTionCloud,
) -> None:
    """PID steps again after its interval, not before."""
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()
    hass.states.async_set(SENSOR, "860")

    await _tick(hass, freezer, 29)
    assert _breezer(cloud).speed == 6

    await _tick(hass, freezer, 1)
    assert _breezer(cloud).speed == 3


async def test_stop_ends_steps(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    manager: TionPidManager,
    cloud: FakeTionCloud,
) -> None:
    """A stopped PID no longer steps."""
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    manager.stop(BREEZER_4S)
    hass.states.async_set(SENSOR, "860")
    await _tick(hass, freezer, 30)

    assert not manager.is_active(BREEZER_4S)
    assert manager.status(BREEZER_4S) is PidStatus.INACTIVE
    assert _breezer(cloud).speed == 6


def _without_speed() -> TionAccount:
    account = default_account()
    device = account.device(BREEZER_4S)
    assert device is not None
    speed = next(iter(dps(PROFILE_4S, fan_speed_level=1)))
    return replace_device(
        account,
        BREEZER_4S,
        dps={code: value for code, value in device.dps.items() if code != speed},
    )


@pytest.mark.parametrize(
    ("account", "options", "sensor_state", "command_error", "status"),
    [
        pytest.param(
            default_account(),
            pid_options(),
            "1200",
            None,
            PidStatus.RUNNING,
            id="running",
        ),
        pytest.param(
            default_account(),
            pid_options(**{CONF_CO2_SENSOR_ENTITY_ID: "sensor.missing"}),
            "1200",
            None,
            PidStatus.PAUSED_SENSOR_UNAVAILABLE,
            id="sensor_missing",
        ),
        pytest.param(
            default_account(),
            pid_options(),
            "unavailable",
            None,
            PidStatus.PAUSED_SENSOR_UNAVAILABLE,
            id="sensor_unavailable",
        ),
        pytest.param(
            default_account(),
            pid_options(),
            "unknown",
            None,
            PidStatus.PAUSED_SENSOR_UNAVAILABLE,
            id="sensor_unknown",
        ),
        pytest.param(
            default_account(),
            pid_options(),
            "high",
            None,
            PidStatus.PAUSED_SENSOR_UNAVAILABLE,
            id="sensor_not_a_number",
        ),
        pytest.param(
            default_account(),
            pid_options(),
            "nan",
            None,
            PidStatus.PAUSED_SENSOR_UNAVAILABLE,
            id="sensor_not_finite",
        ),
        pytest.param(
            replace_device(default_account(), BREEZER_4S, is_online=False),
            pid_options(),
            "1200",
            None,
            PidStatus.PAUSED_DEVICE_UNAVAILABLE,
            id="breezer_offline",
        ),
        pytest.param(
            replace_device(default_account(), MAGICAIR, is_online=False),
            pid_options(),
            "1200",
            None,
            PidStatus.PAUSED_DEVICE_UNAVAILABLE,
            id="gateway_offline",
        ),
        pytest.param(
            _without_speed(),
            pid_options(),
            "1200",
            None,
            PidStatus.PAUSED_INVALID_DEVICE_DATA,
            id="speed_unknown",
        ),
        pytest.param(
            default_account(),
            pid_options(),
            "1200",
            TionConnectionError("down"),
            PidStatus.SEND_FAILED,
            id="command_failed",
        ),
    ],
)
async def test_status(
    hass: HomeAssistant,
    manager: TionPidManager,
    cloud: FakeTionCloud,
    sensor_state: str,
    command_error: Exception | None,
    status: PidStatus,
) -> None:
    """The status tells why PID does not run, without raising."""
    hass.states.async_set(SENSOR, sensor_state)
    cloud.command_error = command_error

    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    assert manager.status(BREEZER_4S) is status
    assert manager.is_active(BREEZER_4S)


@pytest.mark.parametrize(
    "device_id", [BREEZER_4S, MAGICAIR], ids=["breezer", "gateway"]
)
async def test_offline_pause_sends_nothing_then_resumes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    manager: TionPidManager,
    cloud: FakeTionCloud,
    device_id: str,
) -> None:
    """PID sends nothing while the breezer or its gateway is offline, then resumes."""
    hass.states.async_set(SENSOR, "860")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()
    cloud.push(replace_device(cloud.account, device_id, is_online=False))
    await hass.async_block_till_done()
    hass.states.async_set(SENSOR, "1200")

    await _tick(hass, freezer, 30)

    assert _commands(cloud) == []
    assert manager.status(BREEZER_4S) is PidStatus.PAUSED_DEVICE_UNAVAILABLE

    cloud.push(replace_device(cloud.account, device_id, is_online=True))
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)

    assert _breezer(cloud).speed == 6
    assert manager.status(BREEZER_4S) is PidStatus.RUNNING


async def test_no_command_when_speed_holds(
    hass: HomeAssistant, manager: TionPidManager, cloud: FakeTionCloud
) -> None:
    """PID sends nothing while the breezer already runs as it says."""
    hass.states.async_set(SENSOR, "860")

    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    assert _commands(cloud) == []
    assert manager.status(BREEZER_4S) is PidStatus.RUNNING


@pytest.mark.parametrize(
    ("account", "options", "co2", "is_on", "speed"),
    [
        pytest.param(
            default_account(),
            pid_options(**{CONF_PID_MIN_SPEED: 0}),
            "400",
            False,
            3,
            id="turns_off_at_speed_0",
        ),
        pytest.param(
            set_values(default_account(), BREEZER_4S, dps(PROFILE_4S, on_off=False)),
            pid_options(),
            "1200",
            True,
            6,
            id="turns_on",
        ),
    ],
)
async def test_turns_breezer_off_and_on(
    hass: HomeAssistant,
    manager: TionPidManager,
    cloud: FakeTionCloud,
    co2: str,
    is_on: bool,
    speed: int,
) -> None:
    """PID turns the breezer off at speed 0 and on when it needs air."""
    hass.states.async_set(SENSOR, co2)

    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    assert (_breezer(cloud).is_on, _breezer(cloud).speed) == (is_on, speed)
    assert manager.is_active(BREEZER_4S)


async def test_skips_step_while_previous_waits(
    hass: HomeAssistant,
    manager: TionPidManager,
    cloud: FakeTionCloud,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that comes while a command waits is skipped, not queued."""
    release = asyncio.Event()
    sent: list[DeviceCommand] = []

    async def slow_command(command: DeviceCommand) -> None:
        sent.append(command)
        await release.wait()

    monkeypatch.setattr(cloud, "async_command", slow_command)
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await asyncio.sleep(0)

    manager.set_target(BREEZER_4S, 700)
    release.set()
    await hass.async_block_till_done()

    assert len(sent) == 1


async def test_yields_to_room_auto(
    hass: HomeAssistant, manager: TionPidManager, cloud: FakeTionCloud
) -> None:
    """Turning on the room's cloud auto mode stops PID."""
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    cloud.push(
        set_room_auto(cloud.account, BEDROOM_ID, replace(BEDROOM_AUTO, enabled=True))
    )

    assert not manager.is_active(BREEZER_4S)
    assert manager.status(BREEZER_4S) is PidStatus.INACTIVE


async def test_set_target_restarts_integral_and_steps(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    manager: TionPidManager,
    cloud: FakeTionCloud,
) -> None:
    """A new target is saved, clears the integral and applies at once."""
    hass.states.async_set(SENSOR, "860")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    with patch.object(PidController, "reset", autospec=True) as reset:
        manager.set_target(BREEZER_4S, 700)
        await hass.async_block_till_done()

    assert reset.call_count == 1
    assert manager.target(BREEZER_4S) == 700
    assert (
        config_entry.options[CONF_PID_BREEZERS][BREEZER_4S][CONF_PID_TARGET_CO2] == 700
    )
    assert _breezer(cloud).speed == 6


async def test_start_of_running_pid_keeps_integral(
    hass: HomeAssistant, manager: TionPidManager
) -> None:
    """Starting a running PID again only steps."""
    hass.states.async_set(SENSOR, "860")

    with patch.object(PidController, "reset", autospec=True) as reset:
        manager.start(BREEZER_4S)
        manager.start(BREEZER_4S)
        await hass.async_block_till_done()

    assert reset.call_count == 1


async def test_preset_limits(
    hass: HomeAssistant, manager: TionPidManager, cloud: FakeTionCloud
) -> None:
    """A preset's limits replace the own ones until cleared."""
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    manager.set_preset_limits(BREEZER_4S, (1, 2))
    await hass.async_block_till_done()
    assert _breezer(cloud).speed == 2
    assert manager.effective_limits(BREEZER_4S) == (1, 2)
    assert manager.limits(BREEZER_4S) == (1, 6)

    manager.set_preset_limits(BREEZER_4S, None)
    await hass.async_block_till_done()
    assert _breezer(cloud).speed == 6


async def test_set_limits_saves_and_steps(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    manager: TionPidManager,
    cloud: FakeTionCloud,
) -> None:
    """New own limits are saved in the options and apply at once."""
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    manager.set_limits(BREEZER_4S, speed_max=4)
    await hass.async_block_till_done()

    assert manager.limits(BREEZER_4S) == (1, 4)
    assert config_entry.options[CONF_PID_BREEZERS][BREEZER_4S][CONF_PID_MAX_SPEED] == 4
    assert _breezer(cloud).speed == 4


@pytest.mark.parametrize(
    ("options", "limits", "target"),
    [
        pytest.param(pid_options(), (1, 6), 800, id="defaults"),
        pytest.param(
            pid_options(
                **{
                    CONF_PID_MIN_SPEED: 2,
                    CONF_PID_MAX_SPEED: 5,
                    CONF_PID_TARGET_CO2: 900,
                }
            ),
            (2, 5),
            900,
            id="stored",
        ),
    ],
)
async def test_settings(
    manager: TionPidManager, limits: tuple[int, int], target: int
) -> None:
    """Limits and target come from the options, with defaults."""
    assert manager.limits(BREEZER_4S) == limits
    assert manager.target(BREEZER_4S) == target


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="no_options"),
        pytest.param(pid_options(**{CONF_PID_ENABLED: False}), id="disabled"),
        pytest.param(pid_options(**{CONF_CO2_SENSOR_ENTITY_ID: None}), id="no_sensor"),
    ],
)
async def test_breezer_without_pid(manager: TionPidManager) -> None:
    """A breezer without PID set up cannot start it; the rest is harmless."""
    with pytest.raises(ValueError):
        manager.start(BREEZER_4S)
    manager.stop(BREEZER_4S)
    manager.set_preset_limits(BREEZER_4S, (1, 2))
    manager.add_listener(BREEZER_4S, lambda: None)()

    assert not manager.is_configured(BREEZER_4S)
    assert not manager.is_active(BREEZER_4S)
    assert manager.status(BREEZER_4S) is PidStatus.INACTIVE
    assert not manager.is_configured(BREEZER_3S)


async def test_listeners(hass: HomeAssistant, manager: TionPidManager) -> None:
    """Listeners hear start, status changes and stop until they unsubscribe."""
    heard: list[PidStatus] = []
    unsubscribe = manager.add_listener(
        BREEZER_4S, lambda: heard.append(manager.status(BREEZER_4S))
    )
    hass.states.async_set(SENSOR, "unavailable")

    manager.start(BREEZER_4S)
    await hass.async_block_till_done()
    manager.stop(BREEZER_4S)
    unsubscribe()
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    assert heard == [
        PidStatus.RUNNING,
        PidStatus.PAUSED_SENSOR_UNAVAILABLE,
        PidStatus.INACTIVE,
    ]


async def test_unload_stops_pid(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    manager: TionPidManager,
    cloud: FakeTionCloud,
) -> None:
    """Unloading the entry stops every PID and its timer."""
    hass.states.async_set(SENSOR, "1200")
    manager.start(BREEZER_4S)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    sent = len(_commands(cloud))
    hass.states.async_set(SENSOR, "860")
    await _tick(hass, freezer, 30)

    assert not manager.is_active(BREEZER_4S)
    assert len(_commands(cloud)) == sent
