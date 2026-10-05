"""Tests for the location structure and room auto mode messages."""

from pathlib import Path
from uuid import UUID

import pytest

from custom_components.tion.api.exceptions import TionApiError
from custom_components.tion.api.model import AutoControl, Device, Room
from custom_components.tion.api.profiles import DeviceProfile, decode_profiles
from custom_components.tion.api.protobuf import ProtoMessage, encode_varint
from custom_components.tion.api.structure import (
    check_set_auto_control,
    decode_auto_control_changed,
    decode_structure,
    encode_set_auto_control,
)

from .payloads import (  # noqa: TID251
    LEGACY_USER,
    LOCATION_ID,
    PROFILE_4S,
    PROFILE_BS310,
    ROOM_ID,
    auto_control,
    auto_control_changed,
    device,
    error_response,
    location,
    room,
    structure_response,
)

FIXTURE = Path(__file__).parent / "fixtures" / "device_profiles.bin"
UNKNOWN_PROFILE = UUID("0199bbbb-0000-7000-8000-000000000001")
MAC_4S = 0x0000_03FB_39B5_A6EF


@pytest.fixture(scope="module")
def profiles() -> dict[UUID, DeviceProfile]:
    """The recorded profile catalog."""
    return decode_profiles(FIXTURE.read_bytes())


def _home() -> bytes:
    return structure_response(
        location(
            "LOC0000001",
            name="Дом",
            rooms=(
                room(
                    ROOM_ID,
                    "Спальня",
                    auto=auto_control(
                        enabled=True, speed_min=1, speed_max=5, co2_target=800
                    ),
                ),
                room(UUID("0199aaaa-0000-7000-8000-000000000003"), "Прихожая"),
            ),
            devices=(
                device(
                    "BRZ0000001",
                    PROFILE_4S,
                    name="Бризер в спальне",
                    model=32771,
                    parent_id="MAG0000001",
                    firmware=1163,
                    hardware=1,
                    macs=(MAC_4S, MAC_4S, MAC_4S, MAC_4S),
                ),
                device(
                    "MAG0000001",
                    PROFILE_BS310,
                    name="MagicAir",
                    model=16384,
                    submodel=32772,
                    is_gateway=True,
                    is_online=False,
                    created_by=LEGACY_USER,
                    macs=(0x1E33_7534_3651, 0, 0, 0),
                ),
                device("UNK0000001", UNKNOWN_PROFILE, room_id=None),
            ),
        ),
        wstoken="ws-1",
    )


def test_decode_structure(profiles: dict[UUID, DeviceProfile]) -> None:
    """Locations carry rooms with auto mode and devices with their profile."""
    structure = decode_structure(_home(), profiles)

    assert structure.wstoken == "ws-1"
    (home,) = structure.locations
    assert (home.id, home.sid, home.name, home.needs_migration) == (
        LOCATION_ID,
        "LOC0000001",
        "Дом",
        False,
    )
    assert home.rooms == (
        Room(ROOM_ID, "Спальня", AutoControl(True, 1, 5, 800, 1)),
        Room(UUID("0199aaaa-0000-7000-8000-000000000003"), "Прихожая", None),
    )
    breezer, station, unknown = home.devices
    assert breezer == Device(
        id="BRZ0000001",
        name="Бризер в спальне",
        model=32771,
        submodel=0,
        room_id=ROOM_ID,
        parent_id="MAG0000001",
        is_online=True,
        is_gateway=False,
        firmware=1163,
        hardware=1,
        macs=("03:fb:39:b5:a6:ef",),
        is_cloud_stub=False,
        profile_id=PROFILE_4S,
        profile=profiles[PROFILE_4S],
    )
    assert breezer.product_id == "b4s_ble"
    assert (station.is_gateway, station.is_online, station.parent_id) == (
        True,
        False,
        None,
    )
    assert station.macs == ("1e:33:75:34:36:51",)
    assert station.is_cloud_stub is True
    assert (unknown.profile, unknown.product_id, unknown.room_id) == (None, "", None)
    assert structure.profile_ids() == {PROFILE_4S, PROFILE_BS310, UNKNOWN_PROFILE}


def test_legacy_location_needs_migration(
    profiles: dict[UUID, DeviceProfile],
) -> None:
    """A location created by the old-cloud sync still has to be migrated."""
    structure = decode_structure(
        structure_response(location("LOC0000001", created_by=LEGACY_USER)), profiles
    )

    assert structure.locations[0].needs_migration is True


def test_empty_account(profiles: dict[UUID, DeviceProfile]) -> None:
    """An account without locations is a valid, empty structure."""
    structure = decode_structure(structure_response(wstoken="ws"), profiles)

    assert (structure.locations, structure.wstoken) == ((), "ws")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(error_response(3, "denied"), r"failed \(3\): denied", id="error"),
        pytest.param(b"", "unrecognized response", id="neither"),
    ],
)
def test_structure_errors(
    profiles: dict[UUID, DeviceProfile], payload: bytes, message: str
) -> None:
    """Err and unrecognized responses raise TionApiError."""
    with pytest.raises(TionApiError, match=message):
        decode_structure(payload, profiles)


def test_encode_set_auto_control() -> None:
    """The request carries the room Guid and every auto field."""
    request = ProtoMessage.parse(
        encode_set_auto_control(ROOM_ID, AutoControl(True, 2, 4, 900, 2))
    )

    room_id = request.get_message(1)
    assert room_id is not None
    assert room_id.get_guid(1) == ROOM_ID
    assert [request.get_int(field) for field in (2, 3, 4, 5, 6)] == [1, 2, 4, 900, 2]


def test_encode_set_auto_control_keeps_false_enabled() -> None:
    """Disabled is sent explicitly as the zero varint... or omitted; both read false."""
    request = ProtoMessage.parse(
        encode_set_auto_control(ROOM_ID, AutoControl(False, 1, 3, 800, 1))
    )

    assert request.get_bool(2) is False


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(error_response(4, "busy"), r"failed \(4\): busy", id="error"),
        pytest.param(b"", "unrecognized response", id="neither"),
    ],
)
def test_check_set_auto_control_errors(payload: bytes, message: str) -> None:
    """Only an Ok response passes."""
    with pytest.raises(TionApiError, match=message):
        check_set_auto_control(payload)


def test_check_set_auto_control_ok() -> None:
    """Ok = true passes silently."""
    check_set_auto_control(encode_varint(1, 1))


CURRENT_AUTO = AutoControl(False, 1, 5, 800, 2)


@pytest.mark.parametrize(
    ("payload", "current", "expected"),
    [
        pytest.param(
            auto_control_changed(
                ROOM_ID, enabled=True, speed_min=2, speed_max=4, co2_target=700
            ),
            CURRENT_AUTO,
            AutoControl(True, 2, 4, 700, 1),
            id="changed",
        ),
        pytest.param(
            auto_control_changed(
                ROOM_ID,
                enabled=True,
                speed_min=2,
                speed_max=4,
                co2_target=700,
                removed=True,
            ),
            CURRENT_AUTO,
            None,
            id="removed",
        ),
        pytest.param(
            auto_control_changed(ROOM_ID, enabled=True),
            CURRENT_AUTO,
            AutoControl(True, 1, 5, 800, 2),
            id="switch_only",
        ),
        pytest.param(
            auto_control_changed(ROOM_ID, enabled=True),
            None,
            None,
            id="switch_only_without_auto",
        ),
    ],
)
def test_decode_auto_control_changed(
    payload: bytes, current: AutoControl | None, expected: AutoControl | None
) -> None:
    """The event names the room; without params only the switch changes."""
    change = decode_auto_control_changed(payload)

    assert change.room_id == ROOM_ID
    assert change.apply(current) == expected


def test_auto_control_changed_without_room() -> None:
    """An event without a room cannot be applied."""
    with pytest.raises(TionApiError):
        decode_auto_control_changed(encode_varint(3, 1))
