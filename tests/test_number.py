"""Tests for the auto mode settings of stations and the local PID settings."""

from dataclasses import replace
from uuid import UUID

from ha_tests.common import MockConfigEntry, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import Location, Room, TionAccount
from custom_components.tion.const import (
    CONF_PID_BREEZERS,
    CONF_PID_MAX_SPEED,
    CONF_PID_MIN_SPEED,
    CONF_PID_TARGET_CO2,
    DOMAIN,
)
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

from .api.payloads import PROFILE_BS410  # noqa: TID251
from .common import entity_id, pid_options  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_AUTO,
    BEDROOM_ID,
    BREEZER_3S,
    BREEZER_4S,
    BREEZER_O2,
    MAGICAIR,
    MAGICAIR_310,
    MODULE_CO2,
    FakeTionCloud,
    default_account,
    device,
    replace_device,
    set_room_auto,
)

COTTAGE_ROOM_ID = UUID(int=0x201)
COTTAGE_STATION = "CTG0000001"


def _with_cottage() -> TionAccount:
    account = default_account()
    cottage = Location(
        UUID(int=2),
        "LOC0000002",
        "Cottage",
        (Room(COTTAGE_ROOM_ID, "Hall", BEDROOM_AUTO),),
        (
            device(
                COTTAGE_STATION,
                PROFILE_BS410,
                "Cottage MagicAir",
                room_id=COTTAGE_ROOM_ID,
            ),
        ),
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


@pytest.mark.parametrize(
    "options",
    [pytest.param({}, id="without_pid"), pytest.param(pid_options(), id="with_pid")],
)
@pytest.mark.usefixtures("init_integration")
async def test_numbers(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Stations in a room get its auto mode settings; PID breezers their own."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_station_values_follow_the_room(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Station settings show the room's auto mode; breezers have none without PID."""
    assert [
        _state(hass, f"{MAGICAIR}_min_speed_set"),
        _state(hass, f"{MAGICAIR}_max_speed_set"),
        _state(hass, f"{MAGICAIR}_target_co2"),
        _state(hass, f"{MAGICAIR_310}_min_speed_set"),
    ] == ["1", "4", "800", STATE_UNAVAILABLE]
    assert (
        entity_registry.async_get_entity_id(
            Platform.NUMBER, DOMAIN, f"{BREEZER_4S}_min_speed_set"
        )
        is None
    )


@pytest.mark.parametrize(
    ("account", "unique_id", "top"),
    [
        pytest.param(default_account(), f"{MAGICAIR}_max_speed_set", 6, id="4s"),
        pytest.param(
            replace_device(
                replace_device(default_account(), BREEZER_4S, room_id=None),
                BREEZER_O2,
                room_id=BEDROOM_ID,
            ),
            f"{MAGICAIR}_max_speed_set",
            4,
            id="o2",
        ),
        pytest.param(
            replace_device(default_account(), BREEZER_3S, room_id=None),
            f"{MAGICAIR_310}_max_speed_set",
            6,
            id="no_breezer",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_station_speed_limit_range(
    hass: HomeAssistant, unique_id: str, top: int
) -> None:
    """Station speed limits reach the top speed of the room's fastest breezer."""
    state = hass.states.get(entity_id(hass, Platform.NUMBER, unique_id))

    assert state.attributes[ATTR_MAX] == top


@pytest.mark.parametrize(
    ("unique_id", "value", "changes"),
    [
        pytest.param(f"{MAGICAIR}_min_speed_set", 2, {"speed_min": 2}, id="min"),
        pytest.param(f"{MAGICAIR}_max_speed_set", 5, {"speed_max": 5}, id="max"),
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
    """A station setting changes the room's auto mode and shows the new value."""
    await _set(hass, unique_id, value)

    assert cloud.calls == [("auto_control", (BEDROOM_ID, changes))]
    assert _state(hass, unique_id) == str(value)


@pytest.mark.parametrize(
    ("unique_id", "value"),
    [
        pytest.param(f"{MAGICAIR}_min_speed_set", 5, id="min_above_max"),
        pytest.param(f"{MAGICAIR}_max_speed_set", 0, id="max_below_min"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_room_limits_must_stay_ordered(
    hass: HomeAssistant, cloud: FakeTionCloud, unique_id: str, value: float
) -> None:
    """A minimum above the maximum is refused before reaching the cloud."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _set(hass, unique_id, value)

    assert exc_info.value.translation_key == "speed_limits_invalid"
    assert cloud.calls == []


@pytest.mark.usefixtures("init_integration")
async def test_room_auto_removed(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """Without the room's auto mode its settings are unavailable."""
    cloud.push(set_room_auto(cloud.account, BEDROOM_ID, None))
    await hass.async_block_till_done()

    assert _state(hass, f"{MAGICAIR}_target_co2") == STATE_UNAVAILABLE
    assert _state(hass, f"{MAGICAIR}_min_speed_set") == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_stations_of_a_room_share_settings(hass: HomeAssistant) -> None:
    """A speed limit set on one station shows on every station of the room."""
    await _set(hass, f"{MAGICAIR}_min_speed_set", 2)

    assert _state(hass, f"{MODULE_CO2}_min_speed_set") == "2"


@pytest.mark.parametrize("account", [pytest.param(_with_cottage(), id="cottage")])
@pytest.mark.usefixtures("init_integration")
async def test_room_of_another_location(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A station in a second location changes its own room's auto mode."""
    await _set(hass, f"{COTTAGE_STATION}_max_speed_set", 3)

    assert cloud.calls == [("auto_control", (COTTAGE_ROOM_ID, {"speed_max": 3}))]


@pytest.mark.parametrize("options", [pytest.param(pid_options(), id="pid_on_4s")])
@pytest.mark.usefixtures("init_integration")
async def test_pid_numbers(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A breezer with PID shows its PID settings; one without PID has none."""
    assert [
        _state(hass, f"{BREEZER_4S}_min_speed_set"),
        _state(hass, f"{BREEZER_4S}_max_speed_set"),
        _state(hass, f"{BREEZER_4S}_external_target_co2"),
    ] == ["1", "6", "800"]
    assert (
        entity_registry.async_get_entity_id(
            Platform.NUMBER, DOMAIN, f"{BREEZER_3S}_min_speed_set"
        )
        is None
    )


@pytest.mark.parametrize(
    ("unique_id", "value", "key"),
    [
        pytest.param(f"{BREEZER_4S}_min_speed_set", 2, CONF_PID_MIN_SPEED, id="min"),
        pytest.param(f"{BREEZER_4S}_max_speed_set", 4, CONF_PID_MAX_SPEED, id="max"),
        pytest.param(
            f"{BREEZER_4S}_external_target_co2", 700, CONF_PID_TARGET_CO2, id="co2"
        ),
    ],
)
@pytest.mark.parametrize("options", [pytest.param(pid_options(), id="pid_on_4s")])
async def test_set_pid_setting(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    cloud: FakeTionCloud,
    unique_id: str,
    value: float,
    key: str,
) -> None:
    """A PID setting is saved in the options, without the cloud or a reload."""
    await _set(hass, unique_id, value)

    assert init_integration.options[CONF_PID_BREEZERS][BREEZER_4S][key] == value
    assert _state(hass, unique_id) == str(value)
    assert cloud.calls == []
    assert cloud.refreshes == 0


@pytest.mark.parametrize(
    "options",
    [pytest.param(pid_options(**{CONF_PID_MAX_SPEED: 3}), id="pid_max_3")],
)
@pytest.mark.usefixtures("init_integration")
async def test_pid_limits_must_stay_ordered(hass: HomeAssistant) -> None:
    """A PID minimum above the PID maximum is refused."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _set(hass, f"{BREEZER_4S}_min_speed_set", 4)

    assert exc_info.value.translation_key == "speed_limits_invalid"
    assert _state(hass, f"{BREEZER_4S}_min_speed_set") == "1"


@pytest.mark.parametrize(
    ("account", "options"),
    [
        pytest.param(
            replace_device(default_account(), BREEZER_4S, is_online=False),
            pid_options(),
            id="pid_on_offline_4s",
        )
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_pid_numbers_need_no_cloud(hass: HomeAssistant) -> None:
    """PID settings stay available while the breezer is offline."""
    assert _state(hass, f"{BREEZER_4S}_max_speed_set") == "6"


@pytest.mark.parametrize(
    ("options", "unique_id", "top"),
    [
        pytest.param(pid_options(), f"{BREEZER_4S}_max_speed_set", 6, id="4s"),
        pytest.param(
            pid_options(BREEZER_O2), f"{BREEZER_O2}_max_speed_set", 4, id="o2"
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_pid_speed_limit_range(
    hass: HomeAssistant, unique_id: str, top: int
) -> None:
    """PID speed limits reach the breezer's top speed."""
    state = hass.states.get(entity_id(hass, Platform.NUMBER, unique_id))

    assert state.attributes[ATTR_MAX] == top
