"""The integration on a real TionCloud over fake gRPC and NATS."""

from typing import Any
from unittest.mock import patch

from ha_tests.common import MockConfigEntry
import pytest

from custom_components.tion.api import TionCloud, TionDeviceKey, TionTokens
from custom_components.tion.api.datapoints import DPKind, DPValue
from custom_components.tion.api.nats import TaskFactory
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant

from .api.fakes import (  # noqa: TID251
    BREEZER,
    SID,
    STATION,
    FakeAuth as FakeSession,
    FakeBroker,
    FakeTransport,
)
from .api.payloads import state_report  # noqa: TID251
from .common import entity_id  # noqa: TID251
from .fake_cloud import FakeAuth  # noqa: TID251


@pytest.fixture
def broker() -> FakeBroker:
    """The simulated devices behind the fake NATS."""
    return FakeBroker()


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, broker: FakeBroker
) -> MockConfigEntry:
    """Set up the entry on a real TionCloud and the entry's task factory."""

    async def create_cloud(
        hass: HomeAssistant,
        device_key: TionDeviceKey,
        tokens: TionTokens,
        create_task: TaskFactory,
    ) -> tuple[FakeAuth, TionCloud]:
        cloud = TionCloud(
            FakeTransport(),
            FakeSession(),
            create_task=create_task,
            connect=broker.connect,
        )
        return FakeAuth(), cloud

    with patch("custom_components.tion.async_create_cloud", side_effect=create_cloud):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry


def _state(hass: HomeAssistant, platform: Platform, unique_id: str) -> Any:
    return hass.states.get(entity_id(hass, platform, unique_id))


async def test_state_command_push_and_unload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, broker: FakeBroker
) -> None:
    """Polled state shows up, a command is confirmed, a push lands, unload stops."""
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert _state(hass, Platform.SENSOR, f"{STATION}_co2").state == "405"
    assert _state(hass, Platform.CLIMATE, BREEZER).attributes[ATTR_FAN_MODE] == "1"

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {
            ATTR_ENTITY_ID: entity_id(hass, Platform.CLIMATE, BREEZER),
            ATTR_FAN_MODE: "3",
        },
        blocking=True,
    )
    assert _state(hass, Platform.CLIMATE, BREEZER).attributes[ATTR_FAN_MODE] == "3"

    broker.last.deliver(
        f"hw.tx.{SID}.dps.{STATION}",
        state_report(STATION, DPValue(113, DPKind.INT, 900)),
    )
    await hass.async_block_till_done()
    assert _state(hass, Platform.SENSOR, f"{STATION}_co2").state == "900"

    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    assert broker.last.closed
