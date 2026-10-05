"""Tests for the Tion sensors and the availability of Tion entities."""

from dataclasses import replace
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from ha_tests.common import MockConfigEntry, async_fire_time_changed, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import TionConnectionError
from custom_components.tion.const import DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api.payloads import PROFILE_4S  # noqa: TID251
from .common import entity_id  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BREEZER_4S,
    BREEZER_O2,
    MAGICAIR,
    MAGICAIR_310,
    MODULE_CO2,
    FakeTionCloud,
    default_account,
    dps,
    replace_device,
    set_values,
)

BREEZER_4S_INFLOW = f"{BREEZER_4S}_temperature_in"
BREEZER_O2_INFLOW = f"{BREEZER_O2}_temperature_in"


def _state(hass: HomeAssistant, unique_id: str) -> str:
    return hass.states.get(entity_id(hass, Platform.SENSOR, unique_id)).state


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the sensors."""
    return [Platform.SENSOR]


@pytest.mark.usefixtures("init_integration")
async def test_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Every model gets the sensors its profile has."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_pm25_only_where_the_profile_has_it(
    entity_registry: er.EntityRegistry,
) -> None:
    """The BS410 reports PM2.5; the BS310 and the Module CO2+ do not."""
    assert [
        entity_registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{device}_pm25")
        is not None
        for device in (MAGICAIR, MAGICAIR_310, MODULE_CO2)
    ] == [True, False, False]


@pytest.mark.usefixtures("init_integration")
async def test_push_updates_state(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """A pushed value shows up without a refresh."""
    cloud.push(
        set_values(cloud.account, BREEZER_4S, dps(PROFILE_4S, temp_outdoor=-100))
    )
    await hass.async_block_till_done()

    assert _state(hass, BREEZER_4S_INFLOW) == "-10.0"


@pytest.mark.parametrize(
    ("device_id", "changes", "unavailable", "available"),
    [
        pytest.param(
            BREEZER_O2,
            {"is_online": False},
            BREEZER_O2_INFLOW,
            BREEZER_4S_INFLOW,
            id="device_offline",
        ),
        pytest.param(
            MAGICAIR,
            {"is_online": False},
            BREEZER_4S_INFLOW,
            BREEZER_O2_INFLOW,
            id="gateway_offline",
        ),
        pytest.param(
            BREEZER_O2,
            {"id": "GONE000001"},
            BREEZER_O2_INFLOW,
            BREEZER_4S_INFLOW,
            id="left_the_account",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_unreachable_device(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    device_id: str,
    changes: dict[str, object],
    unavailable: str,
    available: str,
) -> None:
    """A device is unavailable while it or its gateway is offline or gone."""
    cloud.push(replace_device(cloud.account, device_id, **changes))
    await hass.async_block_till_done()

    assert _state(hass, unavailable) == STATE_UNAVAILABLE
    assert _state(hass, available) != STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_unavailable_after_grace(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, cloud: FakeTionCloud
) -> None:
    """Entities go unavailable 60 s after the live channel drops."""
    cloud.push(replace(cloud.account, connected=False))
    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, BREEZER_4S_INFLOW) != STATE_UNAVAILABLE

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, BREEZER_4S_INFLOW) == STATE_UNAVAILABLE

    cloud.push(replace(cloud.account, connected=True))
    await hass.async_block_till_done()
    assert _state(hass, BREEZER_4S_INFLOW) != STATE_UNAVAILABLE


async def test_failed_refresh_keeps_pushed_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: FakeTionCloud
) -> None:
    """With the live channel up, a failed refresh changes nothing."""
    cloud.refresh_error = TionConnectionError("down")

    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, BREEZER_4S_INFLOW) == "-5.2"


@pytest.mark.parametrize(
    "account",
    [
        pytest.param(
            replace_device(default_account(), BREEZER_O2, is_online=False),
            id="o2_offline",
        )
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_device_offline_at_start(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A device offline at startup gets its entities, available once online."""
    assert _state(hass, BREEZER_O2_INFLOW) == STATE_UNAVAILABLE

    cloud.push(replace_device(cloud.account, BREEZER_O2, is_online=True))
    await hass.async_block_till_done()

    assert _state(hass, BREEZER_O2_INFLOW) == "-10.0"
