"""Tests for the Tion room auto mode settings."""

from dataclasses import replace
from uuid import UUID

from ha_tests.common import MockConfigEntry, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import Location, Room, TionAccount
from custom_components.tion.const import DOMAIN
from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .api.payloads import PROFILE_O2  # noqa: TID251
from .common import entity_id  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_AUTO,
    BEDROOM_ID,
    BREEZER_3S,
    BREEZER_4S,
    BREEZER_O2,
    MAGICAIR,
    MAGICAIR_310,
    FakeTionCloud,
    default_account,
    device,
    replace_device,
    set_room_auto,
)

COTTAGE_ROOM_ID = UUID(int=0x201)
COTTAGE_BREEZER = "CTG0000001"


def _with_cottage() -> TionAccount:
    account = default_account()
    cottage = Location(
        UUID(int=2),
        "LOC0000002",
        "Cottage",
        (Room(COTTAGE_ROOM_ID, "Hall", BEDROOM_AUTO),),
        (device(COTTAGE_BREEZER, PROFILE_O2, "Cottage O2", room_id=COTTAGE_ROOM_ID),),
        False,
    )
    return replace(account, locations=(*account.locations, cottage))


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the numbers."""
    return [Platform.NUMBER]


def _state(hass: HomeAssistant, unique_id: str) -> str:
    return hass.states.get(entity_id(hass, Platform.NUMBER, unique_id)).state


async def _set(hass: HomeAssistant, unique_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: entity_id(hass, Platform.NUMBER, unique_id),
            ATTR_VALUE: value,
        },
        blocking=True,
    )


@pytest.mark.usefixtures("init_integration")
async def test_numbers(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Devices in a room get the settings of its auto mode."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_values_follow_the_room(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The settings show the room's auto mode; no room means no settings."""
    assert [
        _state(hass, f"{BREEZER_4S}_min_speed_set"),
        _state(hass, f"{BREEZER_4S}_max_speed_set"),
        _state(hass, f"{MAGICAIR}_target_co2"),
        _state(hass, f"{BREEZER_3S}_min_speed_set"),
        _state(hass, f"{MAGICAIR_310}_target_co2"),
    ] == ["1", "4", "800", STATE_UNAVAILABLE, STATE_UNAVAILABLE]
    assert (
        entity_registry.async_get_entity_id(
            Platform.NUMBER, DOMAIN, f"{BREEZER_O2}_min_speed_set"
        )
        is None
    )


@pytest.mark.usefixtures("init_integration")
async def test_speed_limit_range(hass: HomeAssistant) -> None:
    """Speed limits reach the breezer's top speed."""
    state = hass.states.get(
        entity_id(hass, Platform.NUMBER, f"{BREEZER_4S}_max_speed_set")
    )

    assert state.attributes[ATTR_MAX] == 6


@pytest.mark.parametrize(
    ("unique_id", "value", "changes"),
    [
        pytest.param(f"{BREEZER_4S}_min_speed_set", 2, {"speed_min": 2}, id="min"),
        pytest.param(f"{BREEZER_4S}_max_speed_set", 5, {"speed_max": 5}, id="max"),
        pytest.param(f"{MAGICAIR}_target_co2", 900, {"co2_target": 900}, id="co2"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_set_room_setting(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    unique_id: str,
    value: float,
    changes: dict[str, int],
) -> None:
    """A setting changes the room's auto mode and shows the new value."""
    await _set(hass, unique_id, value)

    assert cloud.calls == [("auto_control", (BEDROOM_ID, changes))]
    assert _state(hass, unique_id) == str(value)


@pytest.mark.usefixtures("init_integration")
async def test_min_above_max_is_rejected(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """The cloud's range check reaches the user as a validation error."""
    cloud.command_error = ValueError("speed_min must not exceed speed_max")

    with pytest.raises(ServiceValidationError) as exc_info:
        await _set(hass, f"{BREEZER_4S}_min_speed_set", 5)

    assert exc_info.value.translation_key == "invalid_value"


@pytest.mark.usefixtures("init_integration")
async def test_room_auto_removed(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """Without the room's auto mode its settings are unavailable."""
    cloud.push(set_room_auto(cloud.account, BEDROOM_ID, None))
    await hass.async_block_till_done()

    assert _state(hass, f"{MAGICAIR}_target_co2") == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    "account",
    [
        pytest.param(
            replace_device(default_account(), BREEZER_O2, room_id=BEDROOM_ID),
            id="two_breezers_in_bedroom",
        )
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_breezers_of_a_room_share_settings(hass: HomeAssistant) -> None:
    """A speed limit set on one breezer shows on every breezer of the room."""
    await _set(hass, f"{BREEZER_4S}_min_speed_set", 2)

    assert _state(hass, f"{BREEZER_O2}_min_speed_set") == "2"


@pytest.mark.parametrize("account", [pytest.param(_with_cottage(), id="cottage")])
@pytest.mark.usefixtures("init_integration")
async def test_room_of_another_location(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A breezer in a second location changes its own room's auto mode."""
    await _set(hass, f"{COTTAGE_BREEZER}_max_speed_set", 3)

    assert cloud.calls == [("auto_control", (COTTAGE_ROOM_ID, {"speed_max": 3}))]
