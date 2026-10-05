"""Tests for the Tion breezer climate entity."""

from dataclasses import replace
from typing import Any

from ha_tests.common import MockConfigEntry, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import DeviceCommand, TionAccount, TionConnectionError
from custom_components.tion.api.datapoints import DPKind, DPValue
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .api.payloads import PROFILE_3S, PROFILE_4S  # noqa: TID251
from .common import entity_id  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_AUTO,
    BEDROOM_ID,
    BREEZER_3S,
    BREEZER_4S,
    BREEZER_O2,
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
