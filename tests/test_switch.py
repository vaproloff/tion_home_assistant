"""Tests for the Tion switches and the errors of Tion commands."""

from ha_tests.common import MockConfigEntry, snapshot_platform
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion.api import (
    DeviceCommand,
    TionApiError,
    TionAuthError,
    TionCommandError,
    TionConnectionError,
)
from custom_components.tion.api.datapoints import DPKind, DPValue
from custom_components.tion.const import DOMAIN
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .api.payloads import PROFILE_3S  # noqa: TID251
from .common import entity_id  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_ID,
    BREEZER_3S,
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


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the switches."""
    return [Platform.SWITCH]


async def _switch(hass: HomeAssistant, unique_id: str, service: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN,
        service,
        {ATTR_ENTITY_ID: entity_id(hass, Platform.SWITCH, unique_id)},
        blocking=True,
    )


@pytest.mark.usefixtures("init_integration")
async def test_switches(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
) -> None:
    """Each model gets the switches it can set."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.parametrize(
    ("unique_id", "exists"),
    [
        pytest.param(f"{BREEZER_4S}_backlight", True, id="4s_backlight"),
        pytest.param(f"{BREEZER_3S}_backlight", False, id="3s_backlight"),
        pytest.param(f"{BREEZER_3S}_sound", False, id="3s_sound"),
        pytest.param(f"{BREEZER_O2}_heater", False, id="o2_without_heater"),
        pytest.param(f"{BREEZER_3S}_heater", True, id="3s_heater"),
        pytest.param(f"{MAGICAIR}_auto_mode", True, id="magicair_auto_mode"),
        pytest.param(f"{MODULE_CO2}_auto_mode", False, id="module_co2_auto_mode"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_switch_per_model(
    entity_registry: er.EntityRegistry, unique_id: str, exists: bool
) -> None:
    """A switch exists only where the model can set it."""
    assert (
        entity_registry.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id)
        is not None
    ) is exists


@pytest.mark.parametrize(
    "account",
    [pytest.param(replace_device(default_account(), BREEZER_3S, dps={}), id="silent")],
)
@pytest.mark.usefixtures("init_integration")
async def test_heater_of_a_silent_breezer(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """Before the breezer reports, its heater switch waits unavailable."""
    heater = entity_id(hass, Platform.SWITCH, f"{BREEZER_3S}_heater")
    assert hass.states.get(heater).state == STATE_UNAVAILABLE

    cloud.push(
        set_values(
            cloud.account,
            BREEZER_3S,
            dps(PROFILE_3S, climatic_flags=0b001, heater_on_off=True),
        )
    )
    await hass.async_block_till_done()

    assert hass.states.get(heater).state == "on"


@pytest.mark.parametrize(
    ("unique_id", "service", "command", "state"),
    [
        pytest.param(
            f"{BREEZER_4S}_backlight",
            SERVICE_TURN_OFF,
            DeviceCommand(BREEZER_4S, (DPValue(76, DPKind.INT, 0),)),
            "off",
            id="4s_backlight",
        ),
        pytest.param(
            f"{BREEZER_4S}_sound",
            SERVICE_TURN_ON,
            DeviceCommand(BREEZER_4S, (DPValue(78, DPKind.INT, 1),)),
            "on",
            id="4s_sound",
        ),
        pytest.param(
            f"{BREEZER_4S}_heater",
            SERVICE_TURN_OFF,
            DeviceCommand(BREEZER_4S, (DPValue(84, DPKind.INT, 1),)),
            "off",
            id="4s_heater",
        ),
        pytest.param(
            f"{BREEZER_3S}_heater",
            SERVICE_TURN_ON,
            DeviceCommand(BREEZER_3S, (DPValue(72, DPKind.BOOL, True),)),
            "on",
            id="3s_heater",
        ),
        pytest.param(
            f"{MAGICAIR}_backlight",
            SERVICE_TURN_OFF,
            DeviceCommand(MAGICAIR, (DPValue(76, DPKind.INT, 0),)),
            "off",
            id="magicair_backlight",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_device_switch_commands(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    unique_id: str,
    service: str,
    command: DeviceCommand,
    state: str,
) -> None:
    """A switch sends its datapoint and shows the confirmed state."""
    await _switch(hass, unique_id, service)

    assert cloud.calls == [("command", command)]
    assert hass.states.get(entity_id(hass, Platform.SWITCH, unique_id)).state == state


@pytest.mark.usefixtures("init_integration")
async def test_auto_mode_commands(hass: HomeAssistant, cloud: FakeTionCloud) -> None:
    """The MagicAir switch turns its room's auto mode on and off."""
    await _switch(hass, f"{MAGICAIR}_auto_mode", SERVICE_TURN_ON)
    await _switch(hass, f"{MAGICAIR}_auto_mode", SERVICE_TURN_OFF)

    assert cloud.calls == [
        ("auto_control", (BEDROOM_ID, {"enabled": True})),
        ("auto_control", (BEDROOM_ID, {"enabled": False})),
    ]


@pytest.mark.usefixtures("init_integration")
async def test_auto_mode_without_room_auto(hass: HomeAssistant) -> None:
    """A MagicAir in a room without auto mode has an unavailable switch."""
    state = hass.states.get(
        entity_id(hass, Platform.SWITCH, f"{MAGICAIR_310}_auto_mode")
    )

    assert state.state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "raised", "translation_key"),
    [
        pytest.param(
            TionCommandError(3, "busy"),
            HomeAssistantError,
            "command_rejected",
            id="rejected",
        ),
        pytest.param(
            TionConnectionError("down"),
            HomeAssistantError,
            "cloud_unavailable",
            id="unavailable",
        ),
        pytest.param(TionApiError("bad"), HomeAssistantError, "cloud_error", id="api"),
        pytest.param(
            ValueError("out of range"),
            ServiceValidationError,
            "invalid_value",
            id="invalid",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_command_errors(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    error: Exception,
    raised: type[HomeAssistantError],
    translation_key: str,
) -> None:
    """Cloud failures surface as translated Home Assistant errors."""
    cloud.command_error = error

    with pytest.raises(raised) as exc_info:
        await _switch(hass, f"{BREEZER_4S}_sound", SERVICE_TURN_ON)

    assert exc_info.value.translation_key == translation_key


@pytest.mark.usefixtures("init_integration")
async def test_command_auth_error_asks_to_sign_in(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A rejected sign-in during a command starts reauthentication."""
    cloud.command_error = TionAuthError("expired")

    with pytest.raises(HomeAssistantError) as exc_info:
        await _switch(hass, f"{BREEZER_4S}_sound", SERVICE_TURN_ON)
    await hass.async_block_till_done()

    assert exc_info.value.translation_key == "auth_failed"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
