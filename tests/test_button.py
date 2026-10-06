"""Tests for the Tion filter reset button."""

from ha_tests.common import MockConfigEntry, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import DeviceCommand
from custom_components.tion.api.datapoints import DPKind, DPValue
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import entity_id  # noqa: TID251
from .fake_cloud import BREEZER_O2, FakeTionCloud  # noqa: TID251


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the buttons."""
    return [Platform.BUTTON]


@pytest.mark.usefixtures("init_integration")
async def test_buttons(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Every breezer, the O2 included, can reset its filter."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_reset_filter(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """Pressing the button writes a full filter life (180 days)."""
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {
            ATTR_ENTITY_ID: entity_id(
                hass, Platform.BUTTON, f"{BREEZER_O2}_reset_filters"
            )
        },
        blocking=True,
    )

    assert cloud.calls == [
        ("command", DeviceCommand(BREEZER_O2, (DPValue(171, DPKind.INT, 15_552_000),)))
    ]
