"""Tests for the Tion filter replacement sensor."""

from ha_tests.common import MockConfigEntry, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components import persistent_notification
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api.payloads import PROFILE_4S, PROFILE_O2  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BREEZER_4S,
    BREEZER_O2,
    FakeTionCloud,
    dps,
    set_values,
)


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the binary sensors."""
    return [Platform.BINARY_SENSOR]


def _notifications(hass: HomeAssistant) -> set[str]:
    # pylint: disable-next=protected-access
    return set(persistent_notification._async_get_or_create_notifications(hass))  # noqa: SLF001


@pytest.mark.usefixtures("init_integration")
async def test_binary_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Every breezer reports whether its filter is due."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_filter_notification(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """A due filter notifies; a replaced one dismisses the notification."""
    assert _notifications(hass) == {f"tion_filter_need_replacement_{BREEZER_O2}"}

    cloud.push(
        set_values(cloud.account, BREEZER_4S, dps(PROFILE_4S, climatic_flags=0b111))
    )
    cloud.push(set_values(cloud.account, BREEZER_O2, dps(PROFILE_O2, climatic_flags=0)))
    await hass.async_block_till_done()

    assert _notifications(hass) == {f"tion_filter_need_replacement_{BREEZER_4S}"}
