"""The integration on a real TionCloud over fake gRPC and NATS."""

from typing import Any
from unittest.mock import patch

from ha_tests.common import MockConfigEntry
import pytest

from custom_components.tion.api import TionCloud, TionDeviceKey, TionTokens
from custom_components.tion.api.datapoints import DPKind, DPValue
from custom_components.tion.api.nats import TaskFactory
from custom_components.tion.const import DOMAIN
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api.fakes import (  # noqa: TID251
    BREEZER,
    MISSING_PROFILE,
    SID,
    STATION,
    FakeAuth as FakeSession,
    FakeBroker,
    FakeTransport,
    cloud_structure,
)
from .api.payloads import ROOM_ID, auto_control_changed, state_report  # noqa: TID251
from .common import PID_SENSOR, entity_id, pid_options  # noqa: TID251
from .fake_cloud import FakeAuth  # noqa: TID251


@pytest.fixture
def broker() -> FakeBroker:
    """The simulated devices behind the fake NATS."""
    return FakeBroker()


@pytest.fixture
def structure() -> bytes:
    """The structure the cloud answers."""
    return cloud_structure()


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    broker: FakeBroker,
    structure: bytes,
) -> MockConfigEntry:
    """Set up the entry on a real TionCloud and the entry's task factory."""

    async def create_cloud(
        hass: HomeAssistant,
        device_key: TionDeviceKey,
        tokens: TionTokens,
        create_task: TaskFactory,
    ) -> tuple[FakeAuth, TionCloud]:
        transport = FakeTransport()
        transport.answers["GetFullStructureLocations"] = [structure]
        cloud = TionCloud(
            transport,
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


async def _set_fan_mode(hass: HomeAssistant, fan_mode: str) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {
            ATTR_ENTITY_ID: entity_id(hass, Platform.CLIMATE, BREEZER),
            ATTR_FAN_MODE: fan_mode,
        },
        blocking=True,
    )


@pytest.mark.parametrize(
    "structure",
    [
        pytest.param(
            cloud_structure(breezer_profile=MISSING_PROFILE, breezer_model=32771),
            id="missing_profile",
        )
    ],
)
async def test_breezer_whose_profile_left_the_catalog(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A 4S whose profile Tion dropped from the catalog is set up and obeys commands."""
    device_entry = device_registry.async_get_device_by_identifier(
        (DOMAIN, BREEZER), loaded_entry.entry_id
    )
    assert device_entry is not None
    assert device_entry.model == "Breezer 4S"
    assert _state(hass, Platform.CLIMATE, BREEZER).attributes[ATTR_FAN_MODE] == "1"

    await _set_fan_mode(hass, "3")

    assert _state(hass, Platform.CLIMATE, BREEZER).attributes[ATTR_FAN_MODE] == "3"


@pytest.mark.parametrize("options", [pytest.param(pid_options(BREEZER), id="pid")])
async def test_local_pid_drives_the_breezer_until_room_auto(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, broker: FakeBroker
) -> None:
    """Local PID sets the speed over NATS and yields to the room's auto mode."""
    hass.states.async_set(PID_SENSOR, "1200")

    await _set_fan_mode(hass, "local_pid")
    await hass.async_block_till_done()

    attributes = _state(hass, Platform.CLIMATE, BREEZER).attributes
    assert (attributes[ATTR_FAN_MODE], attributes["speed"]) == ("local_pid", 6)

    broker.last.deliver(
        f"app.location.{SID}.AutoControlChanged",
        auto_control_changed(
            ROOM_ID, enabled=True, speed_min=1, speed_max=5, co2_target=800
        ),
    )
    await hass.async_block_till_done()

    attributes = _state(hass, Platform.CLIMATE, BREEZER).attributes
    assert (attributes[ATTR_FAN_MODE], attributes["pid_active"]) == ("auto", False)
