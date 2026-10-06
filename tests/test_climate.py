"""Tests for the Tion breezer climate entity."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from ha_tests.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
    snapshot_platform,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import DeviceCommand, TionAccount, TionConnectionError
from custom_components.tion.api.datapoints import DPKind, DPValue
from custom_components.tion.const import CONF_PRESETS
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    Platform,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .api.payloads import PROFILE_3S, PROFILE_4S  # noqa: TID251
from .common import PID_SENSOR, entity_id, pid_options, setup_entry  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_AUTO,
    BEDROOM_ID,
    BREEZER_3S,
    BREEZER_4S,
    BREEZER_O2,
    FakeAuth,
    FakeTionCloud,
    default_account,
    dps,
    set_room_auto,
    set_values,
)

AUTO_ON = set_room_auto(
    default_account(), BEDROOM_ID, replace(BEDROOM_AUTO, enabled=True)
)


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the climate entities."""
    return [Platform.CLIMATE]


def _attributes(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    return dict(
        hass.states.get(entity_id(hass, Platform.CLIMATE, device_id)).attributes
    )


async def _call(hass: HomeAssistant, service: str, device_id: str, **data: Any) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        service,
        {ATTR_ENTITY_ID: entity_id(hass, Platform.CLIMATE, device_id), **data},
        blocking=True,
    )


def _command(device_id: str, *values: tuple[int, DPKind, Any]) -> tuple[str, Any]:
    return (
        "command",
        DeviceCommand(device_id, tuple(DPValue(*value) for value in values)),
    )


@pytest.mark.usefixtures("init_integration")
async def test_climate(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Every breezer gets a climate entity named after it."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.parametrize(
    ("device_id", "hvac_modes", "features"),
    [
        pytest.param(
            BREEZER_4S,
            [HVACMode.OFF, HVACMode.FAN_ONLY, HVACMode.HEAT],
            ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.SWING_MODE,
            id="4s_with_heater",
        ),
        pytest.param(
            BREEZER_O2,
            [HVACMode.OFF, HVACMode.FAN_ONLY],
            ClimateEntityFeature.SWING_MODE,
            id="o2_without_heater",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_heater_decides_modes(
    hass: HomeAssistant,
    device_id: str,
    hvac_modes: list[HVACMode],
    features: ClimateEntityFeature,
) -> None:
    """Heating and the target temperature exist only with a heater."""
    attributes = _attributes(hass, device_id)

    assert attributes["hvac_modes"] == hvac_modes
    assert attributes[ATTR_SUPPORTED_FEATURES] == (
        ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | features
    )


@pytest.mark.parametrize(
    ("account", "device_id", "hvac_mode", "hvac_action"),
    [
        pytest.param(
            default_account(),
            BREEZER_4S,
            HVACMode.HEAT,
            HVACAction.HEATING,
            id="4s_heater_working",
        ),
        pytest.param(
            set_values(
                default_account(),
                BREEZER_4S,
                dps(PROFILE_4S, sensor_heater_power_percent=0),
            ),
            BREEZER_4S,
            HVACMode.HEAT,
            HVACAction.FAN,
            id="4s_heater_idle",
        ),
        pytest.param(
            set_values(
                default_account(),
                BREEZER_3S,
                dps(PROFILE_3S, on_off=True, heater_on_off=True),
            ),
            BREEZER_3S,
            HVACMode.HEAT,
            HVACAction.HEATING,
            id="3s_heating",
        ),
        pytest.param(
            set_values(default_account(), BREEZER_3S, dps(PROFILE_3S, on_off=True)),
            BREEZER_3S,
            HVACMode.FAN_ONLY,
            HVACAction.FAN,
            id="3s_fan",
        ),
        pytest.param(
            default_account(), BREEZER_3S, HVACMode.OFF, HVACAction.OFF, id="3s_off"
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_hvac_mode_and_action(
    hass: HomeAssistant,
    account: TionAccount,
    device_id: str,
    hvac_mode: HVACMode,
    hvac_action: HVACAction,
) -> None:
    """The 4S heats by its heater power, the others whenever heating is on."""
    state = hass.states.get(entity_id(hass, Platform.CLIMATE, device_id))

    assert (state.state, state.attributes[ATTR_HVAC_ACTION]) == (hvac_mode, hvac_action)


@pytest.mark.parametrize(
    ("account", "device_id", "fan_modes", "fan_mode"),
    [
        pytest.param(
            default_account(),
            BREEZER_4S,
            ["auto", "1", "2", "3", "4", "5", "6"],
            "3",
            id="room_auto_off",
        ),
        pytest.param(
            AUTO_ON,
            BREEZER_4S,
            ["auto", "1", "2", "3", "4", "5", "6"],
            "auto",
            id="room_auto_on",
        ),
        pytest.param(
            default_account(),
            BREEZER_O2,
            ["1", "2", "3", "4"],
            "1",
            id="no_room",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_fan_modes(
    hass: HomeAssistant,
    account: TionAccount,
    device_id: str,
    fan_modes: list[str],
    fan_mode: str,
) -> None:
    """Auto is offered only where the room has an auto mode."""
    attributes = _attributes(hass, device_id)

    assert (attributes[ATTR_FAN_MODES], attributes[ATTR_FAN_MODE]) == (
        fan_modes,
        fan_mode,
    )


@pytest.mark.parametrize(
    ("device_id", "swing_modes", "swing_mode"),
    [
        pytest.param(BREEZER_4S, ["outside", "inside"], "outside", id="4s"),
        pytest.param(BREEZER_3S, ["outside", "inside", "mixed"], "mixed", id="3s"),
        pytest.param(BREEZER_O2, ["outside", "inside"], "inside", id="o2"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_swing_modes(
    hass: HomeAssistant, device_id: str, swing_modes: list[str], swing_mode: str
) -> None:
    """Each model offers the flap positions it can take."""
    attributes = _attributes(hass, device_id)

    assert (attributes[ATTR_SWING_MODES], attributes[ATTR_SWING_MODE]) == (
        swing_modes,
        swing_mode,
    )


@pytest.mark.parametrize(
    ("account", "service", "device_id", "data", "calls"),
    [
        pytest.param(
            default_account(),
            SERVICE_TURN_ON,
            BREEZER_3S,
            {},
            [_command(BREEZER_3S, (70, DPKind.BOOL, True))],
            id="turn_on",
        ),
        pytest.param(
            default_account(),
            SERVICE_TURN_OFF,
            BREEZER_4S,
            {},
            [_command(BREEZER_4S, (70, DPKind.BOOL, False))],
            id="turn_off",
        ),
        pytest.param(
            AUTO_ON,
            SERVICE_TURN_OFF,
            BREEZER_4S,
            {},
            [
                ("auto_control", (BEDROOM_ID, {"enabled": False})),
                _command(BREEZER_4S, (70, DPKind.BOOL, False)),
            ],
            id="turn_off_leaves_room_auto",
        ),
        pytest.param(
            AUTO_ON,
            SERVICE_SET_HVAC_MODE,
            BREEZER_4S,
            {ATTR_HVAC_MODE: HVACMode.OFF},
            [
                ("auto_control", (BEDROOM_ID, {"enabled": False})),
                _command(BREEZER_4S, (70, DPKind.BOOL, False)),
            ],
            id="hvac_off_leaves_room_auto",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_HVAC_MODE,
            BREEZER_4S,
            {ATTR_HVAC_MODE: HVACMode.FAN_ONLY},
            [_command(BREEZER_4S, (70, DPKind.BOOL, True), (84, DPKind.INT, 1))],
            id="fan_only_forbids_heating",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_HVAC_MODE,
            BREEZER_3S,
            {ATTR_HVAC_MODE: HVACMode.HEAT},
            [_command(BREEZER_3S, (70, DPKind.BOOL, True), (72, DPKind.BOOL, True))],
            id="heat",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_HVAC_MODE,
            BREEZER_O2,
            {ATTR_HVAC_MODE: HVACMode.FAN_ONLY},
            [_command(BREEZER_O2, (70, DPKind.BOOL, True))],
            id="fan_only_without_heater",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_TEMPERATURE,
            BREEZER_4S,
            {ATTR_TEMPERATURE: 20},
            [_command(BREEZER_4S, (130, DPKind.INT, 200))],
            id="temperature",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_TEMPERATURE,
            BREEZER_3S,
            {ATTR_TEMPERATURE: 22, ATTR_HVAC_MODE: HVACMode.HEAT},
            [
                _command(BREEZER_3S, (70, DPKind.BOOL, True), (72, DPKind.BOOL, True)),
                _command(BREEZER_3S, (130, DPKind.INT, 220)),
            ],
            id="temperature_with_mode",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_FAN_MODE,
            BREEZER_4S,
            {ATTR_FAN_MODE: "2"},
            [_command(BREEZER_4S, (140, DPKind.INT, 2))],
            id="speed",
        ),
        pytest.param(
            AUTO_ON,
            SERVICE_SET_FAN_MODE,
            BREEZER_4S,
            {ATTR_FAN_MODE: "2"},
            [
                ("auto_control", (BEDROOM_ID, {"enabled": False})),
                _command(BREEZER_4S, (140, DPKind.INT, 2)),
            ],
            id="speed_leaves_room_auto",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_FAN_MODE,
            BREEZER_4S,
            {ATTR_FAN_MODE: "auto"},
            [("auto_control", (BEDROOM_ID, {"enabled": True}))],
            id="room_auto",
        ),
        pytest.param(
            default_account(),
            SERVICE_SET_SWING_MODE,
            BREEZER_4S,
            {ATTR_SWING_MODE: "inside"},
            [_command(BREEZER_4S, (195, DPKind.INT, 1))],
            id="swing",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_commands(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    account: TionAccount,
    service: str,
    device_id: str,
    data: dict[str, Any],
    calls: list[tuple[str, Any]],
) -> None:
    """Each action sends its commands, leaving the room's auto mode first."""
    await _call(hass, service, device_id, **data)

    assert cloud.calls == calls


@pytest.mark.usefixtures("init_integration")
async def test_4s_cannot_mix_air(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """The 4S does not offer mixed air, so Home Assistant refuses it."""
    with pytest.raises(ServiceValidationError):
        await _call(
            hass, SERVICE_SET_SWING_MODE, BREEZER_4S, **{ATTR_SWING_MODE: "mixed"}
        )

    assert cloud.calls == []


@pytest.mark.parametrize("account", [pytest.param(AUTO_ON, id="room_auto_on")])
@pytest.mark.usefixtures("init_integration")
async def test_turn_off_stops_when_leaving_auto_fails(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """If the room's auto mode cannot be left, the breezer is not touched."""
    cloud.command_error = TionConnectionError("down")

    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_TURN_OFF, BREEZER_4S)

    assert cloud.calls == [("auto_control", (BEDROOM_ID, {"enabled": False}))]
    assert (
        hass.states.get(entity_id(hass, Platform.CLIMATE, BREEZER_4S)).state == "heat"
    )


@pytest.mark.parametrize("account", [pytest.param(AUTO_ON, id="room_auto_on")])
@pytest.mark.usefixtures("init_integration")
async def test_room_auto_removed_in_the_app(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """Without the room's auto mode the fan shows the breezer's own speed."""
    cloud.push(set_room_auto(cloud.account, BEDROOM_ID, None))
    await hass.async_block_till_done()

    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_FAN_MODES], attributes[ATTR_FAN_MODE]) == (
        ["1", "2", "3", "4", "5", "6"],
        "3",
    )


CLIMATE_4S = "climate.bedroom_breezer_4s"
PRESETS = {
    CONF_PRESETS: {
        BREEZER_4S: {
            "sleep": {"type": "manual", "speed": 1},
            "eco": {"type": "local_pid", "min_speed": 1, "max_speed": 2},
            "away": {"type": "auto", "min_speed": 1, "max_speed": 3},
        }
    }
}
PID_PRESETS = {**pid_options(), **PRESETS}
ON_AT_1 = _command(BREEZER_4S, (70, DPKind.BOOL, True), (140, DPKind.INT, 1))
ON_AT_3 = _command(BREEZER_4S, (70, DPKind.BOOL, True), (140, DPKind.INT, 3))
LEAVE_ROOM_AUTO = ("auto_control", (BEDROOM_ID, {"enabled": False}))


@pytest.mark.parametrize(
    ("options", "fan_modes"),
    [
        pytest.param({}, ["auto", "1", "2", "3", "4", "5", "6"], id="without_pid"),
        pytest.param(
            pid_options(),
            ["auto", "local_pid", "1", "2", "3", "4", "5", "6"],
            id="with_pid",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_local_pid_fan_mode_offered(
    hass: HomeAssistant, fan_modes: list[str]
) -> None:
    """Local PID is a fan mode of breezers that have it set up."""
    assert _attributes(hass, BREEZER_4S)[ATTR_FAN_MODES] == fan_modes


@pytest.mark.parametrize(
    ("account", "options"),
    [pytest.param(AUTO_ON, pid_options(), id="room_auto_on")],
)
@pytest.mark.usefixtures("init_integration")
async def test_local_pid_fan_mode(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """Picking local PID leaves the room's auto mode, then PID sets the speed."""
    hass.states.async_set(PID_SENSOR, "1200")

    await _call(hass, SERVICE_SET_FAN_MODE, BREEZER_4S, **{ATTR_FAN_MODE: "local_pid"})

    assert cloud.calls == [LEAVE_ROOM_AUTO, _command(BREEZER_4S, (140, DPKind.INT, 6))]
    attributes = _attributes(hass, BREEZER_4S)
    assert (
        attributes[ATTR_FAN_MODE],
        attributes["pid_active"],
        attributes["pid_status"],
    ) == ("local_pid", True, "running")


@pytest.mark.parametrize(
    ("service", "data", "calls", "fan_mode"),
    [
        pytest.param(
            SERVICE_SET_FAN_MODE,
            {ATTR_FAN_MODE: "2"},
            [_command(BREEZER_4S, (140, DPKind.INT, 2))],
            "2",
            id="speed",
        ),
        pytest.param(
            SERVICE_SET_FAN_MODE,
            {ATTR_FAN_MODE: "auto"},
            [("auto_control", (BEDROOM_ID, {"enabled": True}))],
            "auto",
            id="room_auto",
        ),
        pytest.param(
            SERVICE_TURN_OFF,
            {},
            [_command(BREEZER_4S, (70, DPKind.BOOL, False))],
            None,
            id="turn_off",
        ),
    ],
)
@pytest.mark.parametrize("options", [pytest.param(pid_options(), id="pid")])
@pytest.mark.usefixtures("init_integration")
async def test_leaving_local_pid_stops_it(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    service: str,
    data: dict[str, Any],
    calls: list[tuple[str, Any]],
    fan_mode: str | None,
) -> None:
    """Another fan mode or turning off stops local PID."""
    hass.states.async_set(PID_SENSOR, "860")
    await _call(hass, SERVICE_SET_FAN_MODE, BREEZER_4S, **{ATTR_FAN_MODE: "local_pid"})

    await _call(hass, service, BREEZER_4S, **data)

    assert cloud.calls == calls
    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_FAN_MODE], attributes["pid_active"]) == (fan_mode, False)


@pytest.mark.parametrize("options", [pytest.param(pid_options(), id="pid")])
@pytest.mark.usefixtures("init_integration")
async def test_room_auto_from_the_app_wins_over_local_pid(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """The room's auto mode turned on elsewhere takes the breezer from PID."""
    hass.states.async_set(PID_SENSOR, "860")
    await _call(hass, SERVICE_SET_FAN_MODE, BREEZER_4S, **{ATTR_FAN_MODE: "local_pid"})

    cloud.push(
        set_room_auto(cloud.account, BEDROOM_ID, replace(BEDROOM_AUTO, enabled=True))
    )
    await hass.async_block_till_done()

    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_FAN_MODE], attributes["pid_active"]) == ("auto", False)


@pytest.mark.parametrize(
    ("options", "preset_modes"),
    [
        pytest.param(PRESETS, ["none", "sleep"], id="without_pid"),
        pytest.param(PID_PRESETS, ["none", "sleep", "eco"], id="with_pid"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_presets_offered(hass: HomeAssistant, preset_modes: list[str]) -> None:
    """PID presets show only with PID set up; old auto presets never."""
    attributes = _attributes(hass, BREEZER_4S)

    assert attributes[ATTR_PRESET_MODES] == preset_modes
    assert attributes[ATTR_PRESET_MODE] == "none"
    assert ClimateEntityFeature.PRESET_MODE in ClimateEntityFeature(
        attributes[ATTR_SUPPORTED_FEATURES]
    )


async def _preset(hass: HomeAssistant, preset_mode: str) -> None:
    await _call(hass, SERVICE_SET_PRESET_MODE, BREEZER_4S, preset_mode=preset_mode)


@pytest.mark.parametrize(
    ("account", "preset", "enter_calls", "leave_calls"),
    [
        pytest.param(
            default_account(), "sleep", [ON_AT_1], [ON_AT_3], id="manual_from_speed"
        ),
        pytest.param(
            set_values(default_account(), BREEZER_4S, dps(PROFILE_4S, on_off=False)),
            "sleep",
            [ON_AT_1],
            [_command(BREEZER_4S, (70, DPKind.BOOL, False), (140, DPKind.INT, 3))],
            id="manual_from_off",
        ),
        pytest.param(
            AUTO_ON,
            "sleep",
            [LEAVE_ROOM_AUTO, ON_AT_1],
            [ON_AT_3],
            id="manual_from_room_auto",
        ),
        pytest.param(
            default_account(),
            "eco",
            [_command(BREEZER_4S, (140, DPKind.INT, 2))],
            [ON_AT_3],
            id="pid_from_speed",
        ),
    ],
)
@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
@pytest.mark.usefixtures("init_integration")
async def test_preset_enter_and_leave(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    preset: str,
    enter_calls: list[tuple[str, Any]],
    leave_calls: list[tuple[str, Any]],
) -> None:
    """A preset runs its regime; leaving it returns to the regime before it."""
    hass.states.async_set(PID_SENSOR, "1200")

    await _preset(hass, preset)
    assert cloud.calls == enter_calls
    assert _attributes(hass, BREEZER_4S)[ATTR_PRESET_MODE] == preset

    cloud.calls.clear()
    await _preset(hass, "none")
    assert cloud.calls == leave_calls
    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_PRESET_MODE], attributes["pid_active"]) == ("none", False)


@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
@pytest.mark.usefixtures("init_integration")
async def test_switching_presets_keeps_first_baseline(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """Going from preset to preset still returns to the regime before the first."""
    hass.states.async_set(PID_SENSOR, "1200")
    await _preset(hass, "sleep")
    await _preset(hass, "eco")
    cloud.calls.clear()

    await _preset(hass, "none")

    assert cloud.calls == [ON_AT_3]


@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
@pytest.mark.usefixtures("init_integration")
async def test_preset_from_local_pid_returns_to_it(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A preset taken from local PID hands the breezer back to PID."""
    hass.states.async_set(PID_SENSOR, "860")
    await _call(hass, SERVICE_SET_FAN_MODE, BREEZER_4S, **{ATTR_FAN_MODE: "local_pid"})
    await _preset(hass, "sleep")
    assert _attributes(hass, BREEZER_4S)["pid_active"] is False
    cloud.calls.clear()

    await _preset(hass, "none")

    assert cloud.calls == [_command(BREEZER_4S, (140, DPKind.INT, 3))]
    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_FAN_MODE], attributes[ATTR_PRESET_MODE]) == (
        "local_pid",
        "none",
    )


async def _pick_speed(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    await _call(hass, SERVICE_SET_FAN_MODE, BREEZER_4S, **{ATTR_FAN_MODE: "2"})


async def _turn_off(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    await _call(hass, SERVICE_TURN_OFF, BREEZER_4S)


async def _room_auto_in_the_app(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    cloud.push(
        set_room_auto(cloud.account, BEDROOM_ID, replace(BEDROOM_AUTO, enabled=True))
    )
    await hass.async_block_till_done()


async def _speed_in_the_app(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    cloud.push(
        set_values(cloud.account, BREEZER_4S, dps(PROFILE_4S, fan_speed_level=4))
    )
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("preset", "bypass"),
    [
        pytest.param("sleep", _pick_speed, id="manual_fan_mode"),
        pytest.param("sleep", _turn_off, id="manual_turn_off"),
        pytest.param("sleep", _room_auto_in_the_app, id="manual_room_auto"),
        pytest.param("sleep", _speed_in_the_app, id="manual_speed_in_the_app"),
        pytest.param("eco", _pick_speed, id="pid_fan_mode"),
        pytest.param("eco", _turn_off, id="pid_turn_off"),
        pytest.param("eco", _room_auto_in_the_app, id="pid_room_auto"),
    ],
)
@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
@pytest.mark.usefixtures("init_integration")
async def test_regime_change_ends_preset(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    preset: str,
    bypass: Callable[[HomeAssistant, FakeTionCloud], Awaitable[None]],
) -> None:
    """Changing the regime past the preset ends it without restoring."""
    hass.states.async_set(PID_SENSOR, "1200")
    await _preset(hass, preset)

    await bypass(hass, cloud)
    cloud.calls.clear()
    await _preset(hass, "none")

    assert _attributes(hass, BREEZER_4S)[ATTR_PRESET_MODE] == "none"
    assert cloud.calls == []


@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
@pytest.mark.usefixtures("init_integration")
async def test_preset_survives_other_settings(hass: HomeAssistant) -> None:
    """Temperature and the flap are not the fan regime: the preset stays."""
    await _preset(hass, "sleep")

    await _call(hass, SERVICE_SET_TEMPERATURE, BREEZER_4S, **{ATTR_TEMPERATURE: 20})
    await _call(hass, SERVICE_SET_SWING_MODE, BREEZER_4S, **{ATTR_SWING_MODE: "inside"})

    assert _attributes(hass, BREEZER_4S)[ATTR_PRESET_MODE] == "sleep"


@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
@pytest.mark.usefixtures("init_integration")
async def test_preset_unchanged_when_the_cloud_fails(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A preset the breezer did not take is not shown as active."""
    cloud.command_error = TionConnectionError("down")

    with pytest.raises(HomeAssistantError):
        await _preset(hass, "sleep")

    assert _attributes(hass, BREEZER_4S)[ATTR_PRESET_MODE] == "none"


AT_SPEED_1 = set_values(
    default_account(), BREEZER_4S, dps(PROFILE_4S, fan_speed_level=1)
)
AT_SPEED_3_ON = {"type": "manual", "speed": 3, "is_on": True}


@pytest.mark.parametrize(
    ("account", "extra", "fan_mode", "preset_mode", "pid_active"),
    [
        pytest.param(
            default_account(),
            {"pid_active": True, "preset_mode": None, "preset_baseline": None},
            "local_pid",
            "none",
            True,
            id="local_pid",
        ),
        pytest.param(
            AUTO_ON,
            {"pid_active": True, "preset_mode": None, "preset_baseline": None},
            "auto",
            "none",
            False,
            id="local_pid_under_room_auto",
        ),
        pytest.param(
            AT_SPEED_1,
            {
                "pid_active": False,
                "preset_mode": "sleep",
                "preset_baseline": AT_SPEED_3_ON,
            },
            "1",
            "sleep",
            False,
            id="manual_preset",
        ),
        pytest.param(
            default_account(),
            {
                "pid_active": False,
                "preset_mode": "sleep",
                "preset_baseline": AT_SPEED_3_ON,
            },
            "3",
            "none",
            False,
            id="manual_preset_changed_meanwhile",
        ),
        pytest.param(
            default_account(),
            {
                "pid_active": True,
                "preset_mode": "eco",
                "preset_baseline": AT_SPEED_3_ON,
            },
            "local_pid",
            "eco",
            True,
            id="pid_preset",
        ),
        pytest.param(
            AT_SPEED_1,
            {
                "pid_active": False,
                "preset_mode": "boost",
                "preset_baseline": AT_SPEED_3_ON,
            },
            "1",
            "none",
            False,
            id="unknown_preset",
        ),
        pytest.param(
            AT_SPEED_1,
            {
                "pid_active": False,
                "preset_mode": "sleep",
                "preset_baseline": {"type": "auto"},
            },
            "1",
            "none",
            False,
            id="old_baseline",
        ),
    ],
)
@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
async def test_restore(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    extra: dict[str, Any],
    fan_mode: str,
    preset_mode: str,
    pid_active: bool,
) -> None:
    """Local PID and the preset come back after a restart, if still valid."""
    hass.states.async_set(PID_SENSOR, "1200")
    mock_restore_cache_with_extra_data(hass, [(State(CLIMATE_4S, "heat"), extra)])

    with patch("custom_components.tion.PLATFORMS", [Platform.CLIMATE]):
        await setup_entry(hass, config_entry, cloud, auth)

    attributes = _attributes(hass, BREEZER_4S)
    assert (
        attributes[ATTR_FAN_MODE],
        attributes[ATTR_PRESET_MODE],
        attributes["pid_active"],
    ) == (fan_mode, preset_mode, pid_active)


@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
async def test_restore_pid_preset_keeps_its_limits(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
) -> None:
    """A restored PID preset keeps PID within the preset's limits."""
    hass.states.async_set(PID_SENSOR, "1200")
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(CLIMATE_4S, "heat"),
                {
                    "pid_active": True,
                    "preset_mode": "eco",
                    "preset_baseline": AT_SPEED_3_ON,
                },
            )
        ],
    )

    with patch("custom_components.tion.PLATFORMS", [Platform.CLIMATE]):
        await setup_entry(hass, config_entry, cloud, auth)

    assert cloud.calls == [_command(BREEZER_4S, (140, DPKind.INT, 2))]


@pytest.mark.parametrize("options", [pytest.param(pid_options(), id="pid")])
async def test_restored_local_pid_waits_for_its_sensor(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
) -> None:
    """After a restart PID keeps waiting for a sensor that loads later."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(CLIMATE_4S, "heat"),
                {"pid_active": True, "preset_mode": None, "preset_baseline": None},
            )
        ],
    )
    with patch("custom_components.tion.PLATFORMS", [Platform.CLIMATE]):
        await setup_entry(hass, config_entry, cloud, auth)
    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_FAN_MODE], attributes["pid_status"]) == (
        "local_pid",
        "paused_sensor_unavailable",
    )

    hass.states.async_set(PID_SENSOR, "1200")
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes["pid_status"], attributes["speed"]) == ("running", 6)


@pytest.mark.parametrize("options", [pytest.param(PID_PRESETS, id="presets")])
async def test_regime_survives_a_reload(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
) -> None:
    """Saving options reloads the entry; PID and the preset carry over."""
    hass.states.async_set(PID_SENSOR, "1200")
    with patch("custom_components.tion.PLATFORMS", [Platform.CLIMATE]):
        await setup_entry(hass, config_entry, cloud, auth)
        await _preset(hass, "eco")
        # The stopped fake stays disconnected; the reload gets a new cloud.
        reloaded = FakeTionCloud(replace(cloud.account, connected=True))

        with patch(
            "custom_components.tion.async_create_cloud",
            return_value=(auth, reloaded),
        ):
            assert await hass.config_entries.async_reload(config_entry.entry_id)
            await hass.async_block_till_done()

    attributes = _attributes(hass, BREEZER_4S)
    assert (attributes[ATTR_PRESET_MODE], attributes["pid_active"]) == ("eco", True)
