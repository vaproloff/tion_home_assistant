"""Tests for the account snapshot lookups."""

from uuid import UUID

import pytest

from custom_components.tion.api.model import (
    AutoControl,
    Device,
    Location,
    Room,
    TionAccount,
)

ROOM = Room(UUID(int=2), "Bedroom", None)


def _device(device_id: str, room_id: UUID | None) -> Device:
    return Device(
        id=device_id,
        name=device_id,
        model=0,
        submodel=0,
        room_id=room_id,
        parent_id=None,
        is_online=True,
        is_gateway=False,
        firmware=0,
        hardware=0,
        macs=(),
        is_cloud_stub=False,
        profile_id=None,
        profile=None,
    )


def test_account_lookups() -> None:
    """Devices, rooms and locations are found across locations."""
    first = _device("DEV0000001", ROOM.id)
    second = _device("DEV0000002", None)
    home = Location(UUID(int=1), "LOC0000001", "Home", (ROOM,), (first,), False)
    cottage = Location(UUID(int=3), "LOC0000002", "Cottage", (), (second,), False)
    account = TionAccount((home, cottage), connected=True)

    assert list(account.devices()) == [first, second]
    assert account.device("DEV0000002") is second
    assert account.device("missing") is None
    assert account.room(ROOM.id) is ROOM
    assert account.room_of(first) is ROOM
    assert account.room_of(second) is None
    assert account.location_of(second) is cottage


SET_UP = AutoControl(True, 1, 4, 800)


@pytest.mark.parametrize(
    ("auto", "configured"),
    [
        pytest.param(SET_UP, SET_UP, id="set_up"),
        pytest.param(AutoControl(False, 0, 0, 0, 0), None, id="empty_message"),
        pytest.param(None, None, id="absent"),
    ],
)
def test_configured_auto(
    auto: AutoControl | None, configured: AutoControl | None
) -> None:
    """Only an auto mode with a speed range counts as set up."""
    assert Room(UUID(int=2), "Bedroom", auto).configured_auto == configured


def test_empty_account() -> None:
    """A fresh account has nothing and is not connected."""
    account = TionAccount()

    assert (list(account.devices()), account.connected) == ([], False)
    assert account.location_of(_device("DEV0000001", None)) is None
