"""Tests for the semantic device views and command building."""

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from custom_components.tion.api.datapoints import DPKind, DPValue
from custom_components.tion.api.model import Device
from custom_components.tion.api.profiles import DeviceProfile, decode_profiles
from custom_components.tion.api.views import (
    FILTER_RESOURCE_SECONDS,
    Breezer,
    DeviceCommand,
    Flap,
    Station,
    view,
)

from .payloads import (  # noqa: TID251
    PROFILE_3S,
    PROFILE_4S,
    PROFILE_BS310,
    PROFILE_BS410,
    PROFILE_CLEVER,
    PROFILE_CO2,
    PROFILE_O2,
)

FIXTURE = Path(__file__).parent / "fixtures" / "device_profiles.bin"
DEVICE_ID = "DEV0000001"

# Breezer 4S state as reported live (stage 3C), plus heater datapoints (3D).
RECORDED_4S = (
    DPValue(10, DPKind.FLAGS, 0x13),
    DPValue(11, DPKind.FLAGS, 0x3),
    DPValue(70, DPKind.BOOL, True),
    DPValue(76, DPKind.INT, 0),
    DPValue(78, DPKind.INT, 0),
    DPValue(84, DPKind.INT, 0),
    DPValue(100, DPKind.INT, 190),
    DPValue(101, DPKind.INT, 210),
    DPValue(130, DPKind.INT, 150),
    DPValue(140, DPKind.INT, 1),
    DPValue(171, DPKind.INT, 2466193),
    DPValue(180, DPKind.INT, 45),
    DPValue(195, DPKind.INT, 0),
    DPValue(200, DPKind.INT, 1),
    DPValue(202, DPKind.BOOL, True),
)
# MagicAir state as reported live (stage 3C).
RECORDED_MAGICAIR = (
    DPValue(10, DPKind.FLAGS, 0x3),
    DPValue(76, DPKind.INT, 1),
    DPValue(100, DPKind.INT, 244),
    DPValue(110, DPKind.INT, 387),
    DPValue(113, DPKind.INT, 405),
)


@pytest.fixture(scope="module")
def profiles() -> dict[UUID, DeviceProfile]:
    """The recorded profile catalog."""
    return decode_profiles(FIXTURE.read_bytes())


def _device(
    profiles: dict[UUID, DeviceProfile], profile_id: UUID, *values: DPValue
) -> Device:
    return Device(
        id=DEVICE_ID,
        name="Device",
        model=0,
        submodel=0,
        room_id=None,
        parent_id=None,
        is_online=True,
        is_gateway=False,
        firmware=0,
        hardware=0,
        macs=(),
        is_cloud_stub=False,
        profile_id=profile_id,
        profile=profiles[profile_id],
        dps={value.dp_id: value for value in values},
    )


def _breezer(
    profiles: dict[UUID, DeviceProfile], profile_id: UUID, *values: DPValue
) -> Breezer:
    device_view = view(_device(profiles, profile_id, *values))
    assert isinstance(device_view, Breezer)
    return device_view


def _station(
    profiles: dict[UUID, DeviceProfile], profile_id: UUID, *values: DPValue
) -> Station:
    device_view = view(_device(profiles, profile_id, *values))
    assert isinstance(device_view, Station)
    return device_view


@pytest.mark.parametrize(
    ("profile_id", "expected"),
    [
        pytest.param(PROFILE_4S, Breezer, id="4s"),
        pytest.param(PROFILE_3S, Breezer, id="3s"),
        pytest.param(PROFILE_O2, Breezer, id="o2"),
        pytest.param(PROFILE_BS310, Station, id="bs310"),
        pytest.param(PROFILE_BS410, Station, id="bs410"),
        pytest.param(PROFILE_CO2, Station, id="co2_plus"),
    ],
)
def test_view_by_product(
    profiles: dict[UUID, DeviceProfile], profile_id: UUID, expected: type
) -> None:
    """Supported products get their view."""
    assert type(view(_device(profiles, profile_id))) is expected


def test_unsupported_product_has_no_view(
    profiles: dict[UUID, DeviceProfile],
) -> None:
    """Products the integration does not support have no view."""
    assert view(_device(profiles, PROFILE_CLEVER)) is None


def test_device_without_profile_has_no_view(
    profiles: dict[UUID, DeviceProfile],
) -> None:
    """A device whose profile is unknown has no view."""
    orphan = replace(_device(profiles, PROFILE_4S), profile=None)

    assert view(orphan) is None


def test_recorded_4s_state(profiles: dict[UUID, DeviceProfile]) -> None:
    """Live 4S values read in natural units."""
    breezer = _breezer(profiles, PROFILE_4S, *RECORDED_4S)

    assert breezer.id == DEVICE_ID
    assert breezer.is_on is True
    assert breezer.speed == 1
    assert breezer.speed_max == 6
    assert breezer.target_temperature == 15.0
    assert breezer.target_temperature_range == (0.0, 30.0)
    assert breezer.temperature_outdoor == 19.0
    assert breezer.temperature_outlet == 21.0
    assert breezer.heater_installed is True
    assert breezer.heater_enabled is True
    assert breezer.heater_active is True
    assert breezer.heater_power == 45
    assert breezer.heater_type == 1
    assert breezer.flap is Flap.OUTSIDE
    assert breezer.filter_remaining == 2466193
    assert breezer.filter_needs_replacement is False
    assert breezer.backlight is False
    assert breezer.sound is False


def test_unreported_values_are_none(profiles: dict[UUID, DeviceProfile]) -> None:
    """Before the device reports, every value is unknown."""
    breezer = _breezer(profiles, PROFILE_4S)

    assert [
        breezer.is_on,
        breezer.speed,
        breezer.target_temperature,
        breezer.temperature_outdoor,
        breezer.heater_installed,
        breezer.heater_enabled,
        breezer.heater_active,
        breezer.flap,
        breezer.filter_remaining,
        breezer.backlight,
    ] == [None] * 10


@pytest.mark.parametrize(
    ("level", "enabled"),
    [
        pytest.param(0, True, id="allowed"),
        pytest.param(1, False, id="off"),
        pytest.param(3, False, id="other_level"),
    ],
)
def test_4s_heater_level(
    profiles: dict[UUID, DeviceProfile], level: int, enabled: bool
) -> None:
    """On a 4S heating is allowed only at level 0."""
    breezer = _breezer(profiles, PROFILE_4S, DPValue(84, DPKind.INT, level))

    assert breezer.heater_enabled is enabled


@pytest.mark.parametrize(
    ("climatic", "heater_on", "enabled"),
    [
        pytest.param(0b011, True, True, id="heating"),
        pytest.param(0b001, False, False, id="installed_off"),
    ],
)
def test_3s_heater(
    profiles: dict[UUID, DeviceProfile],
    climatic: int,
    heater_on: bool,
    enabled: bool,
) -> None:
    """3S reads the heater switch as a bool and activity from the flags."""
    breezer = _breezer(
        profiles,
        PROFILE_3S,
        DPValue(11, DPKind.FLAGS, climatic),
        DPValue(72, DPKind.BOOL, enabled),
    )

    assert (
        breezer.heater_installed,
        breezer.heater_active,
        breezer.heater_enabled,
    ) == (
        True,
        heater_on,
        enabled,
    )


def test_filter_needs_replacement_flag(profiles: dict[UUID, DeviceProfile]) -> None:
    """Bit 2 of climatic_flags asks for a new filter."""
    breezer = _breezer(profiles, PROFILE_O2, DPValue(11, DPKind.FLAGS, 0b100))

    assert breezer.filter_needs_replacement is True


@pytest.mark.parametrize(
    ("profile_id", "values", "speed_max"),
    [
        pytest.param(PROFILE_4S, (DPValue(150, DPKind.INT, 4),), 4, id="reported"),
        pytest.param(PROFILE_4S, (DPValue(150, DPKind.INT, 0),), 6, id="zero_4s"),
        pytest.param(PROFILE_3S, (), 6, id="fallback_3s"),
        pytest.param(PROFILE_O2, (), 4, id="fallback_o2"),
    ],
)
def test_speed_max(
    profiles: dict[UUID, DeviceProfile],
    profile_id: UUID,
    values: tuple[DPValue, ...],
    speed_max: int,
) -> None:
    """The reported maximum wins; otherwise the model's table value."""
    assert _breezer(profiles, profile_id, *values).speed_max == speed_max


def test_unknown_flap_value(profiles: dict[UUID, DeviceProfile]) -> None:
    """A flap mode outside the known ones reads as unknown."""
    breezer = _breezer(profiles, PROFILE_4S, DPValue(195, DPKind.INT, 7))

    assert breezer.flap is None


@pytest.mark.parametrize(
    ("profile_id", "feature", "supported"),
    [
        pytest.param(PROFILE_4S, "sound", True, id="4s_sound"),
        pytest.param(PROFILE_3S, "sound", False, id="3s_sound"),
        pytest.param(PROFILE_O2, "backlight", False, id="o2_backlight"),
        pytest.param(PROFILE_4S, "heater_power", True, id="4s_heater_power"),
        pytest.param(PROFILE_3S, "heater_power", False, id="3s_heater_power"),
        pytest.param(PROFILE_BS410, "pm25", True, id="bs410_pm25"),
        pytest.param(PROFILE_BS310, "pm25", False, id="bs310_pm25"),
        pytest.param(PROFILE_CO2, "backlight", True, id="co2_backlight"),
    ],
)
def test_supports(
    profiles: dict[UUID, DeviceProfile],
    profile_id: UUID,
    feature: str,
    supported: bool,
) -> None:
    """Features follow the model's profile."""
    device_view = view(_device(profiles, profile_id))

    assert device_view is not None
    assert device_view.supports(feature) is supported


def test_recorded_magicair_state(profiles: dict[UUID, DeviceProfile]) -> None:
    """Live MagicAir values read in natural units."""
    station = _station(profiles, PROFILE_BS310, *RECORDED_MAGICAIR)

    assert (station.co2, station.temperature, station.humidity) == (405, 24.4, 38.7)
    assert station.backlight is True
    assert station.pm25 is None


def test_bs410_pm25(profiles: dict[UUID, DeviceProfile]) -> None:
    """BS410 reports PM2.5."""
    station = _station(profiles, PROFILE_BS410, DPValue(231, DPKind.INT, 12))

    assert station.pm25 == 12


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param({"is_on": False}, (DPValue(70, DPKind.BOOL, False),), id="off"),
        pytest.param({"speed": 3}, (DPValue(140, DPKind.INT, 3),), id="speed"),
        pytest.param(
            {"target_temperature": 20.5},
            (DPValue(130, DPKind.INT, 205),),
            id="target",
        ),
        pytest.param(
            {"heater_enabled": True}, (DPValue(84, DPKind.INT, 0),), id="heater_on"
        ),
        pytest.param(
            {"heater_enabled": False}, (DPValue(84, DPKind.INT, 1),), id="heater_off"
        ),
        pytest.param({"flap": Flap.INSIDE}, (DPValue(195, DPKind.INT, 1),), id="flap"),
        pytest.param({"backlight": True}, (DPValue(76, DPKind.INT, 1),), id="light"),
        pytest.param({"sound": False}, (DPValue(78, DPKind.INT, 0),), id="sound"),
        pytest.param(
            {"filter_reset": True},
            (DPValue(171, DPKind.INT, FILTER_RESOURCE_SECONDS),),
            id="filter_reset",
        ),
        pytest.param(
            {"is_on": True, "speed": 2},
            (DPValue(70, DPKind.BOOL, True), DPValue(140, DPKind.INT, 2)),
            id="several_in_order",
        ),
    ],
)
def test_4s_commands(
    profiles: dict[UUID, DeviceProfile],
    changes: dict[str, Any],
    expected: tuple[DPValue, ...],
) -> None:
    """4S commands become typed datapoint values."""
    command = _breezer(profiles, PROFILE_4S).command(**changes)

    assert command == DeviceCommand(DEVICE_ID, expected)


def test_3s_heater_command(profiles: dict[UUID, DeviceProfile]) -> None:
    """3S switches the heater with its bool datapoint."""
    command = _breezer(profiles, PROFILE_3S).command(heater_enabled=True)

    assert command.values == (DPValue(72, DPKind.BOOL, True),)


@pytest.mark.parametrize(
    ("profile_id", "changes"),
    [
        pytest.param(PROFILE_3S, {"sound": True}, id="unsupported"),
        pytest.param(PROFILE_4S, {"speed": 7}, id="speed_above_max"),
        pytest.param(PROFILE_4S, {"speed": -1}, id="negative_speed"),
        pytest.param(PROFILE_4S, {"target_temperature": 31}, id="too_warm"),
        pytest.param(PROFILE_4S, {"target_temperature": -1}, id="too_cold"),
        pytest.param(PROFILE_4S, {"filter_reset": False}, id="filter_reset_false"),
        pytest.param(PROFILE_4S, {"turbo": True}, id="unknown_field"),
        pytest.param(PROFILE_4S, {}, id="nothing"),
    ],
)
def test_breezer_command_errors(
    profiles: dict[UUID, DeviceProfile], profile_id: UUID, changes: dict[str, Any]
) -> None:
    """Commands the model cannot take are programming errors."""
    breezer = _breezer(profiles, profile_id)

    with pytest.raises(ValueError):
        breezer.command(**changes)


def test_station_commands(profiles: dict[UUID, DeviceProfile]) -> None:
    """A station only switches its light."""
    station = _station(profiles, PROFILE_BS310)

    assert station.command(backlight=False) == DeviceCommand(
        DEVICE_ID, (DPValue(76, DPKind.INT, 0),)
    )
    with pytest.raises(ValueError):
        station.command(speed=1)


@pytest.mark.parametrize(
    ("profile_id", "dp_ids"),
    [
        pytest.param(
            PROFILE_4S,
            [10, 11, 70, 76, 78, 84, 100, 101, 130, 140, 150, 171, 180, 195, 200, 202],
            id="4s",
        ),
        pytest.param(
            PROFILE_3S,
            [10, 11, 70, 72, 100, 101, 130, 140, 150, 171, 195, 200],
            id="3s",
        ),
        pytest.param(PROFILE_BS410, [10, 76, 100, 110, 113, 231], id="bs410"),
    ],
)
def test_query_dp_ids(
    profiles: dict[UUID, DeviceProfile], profile_id: UUID, dp_ids: list[int]
) -> None:
    """A device is polled for the datapoints its view reads."""
    device_view = view(_device(profiles, profile_id))

    assert device_view is not None
    assert device_view.query_dp_ids() == dp_ids
