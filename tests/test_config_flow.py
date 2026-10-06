"""Tests for Tion config and options flows."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from ha_tests.common import MockConfigEntry
import pytest
import voluptuous as vol  # noqa: TID251

from custom_components.tion import config_flow
from custom_components.tion.api import TionAccount
from custom_components.tion.api.auth import (
    LOGIN_ERROR_CODE_EXPIRED,
    LOGIN_ERROR_INVALID_AUTH,
    LOGIN_ERROR_INVALID_CAPTCHA,
    LOGIN_ERROR_INVALID_CODE,
    LOGIN_ERROR_PASSWORD_NOT_SET,
    TionLoginError,
    TionTokens,
)
from custom_components.tion.api.device_key import TionDeviceKey
from custom_components.tion.api.exceptions import TionApiError, TionConnectionError
from custom_components.tion.config_flow import (
    CONF_PRESET_NAME,
    TionConfigFlow,
    TionOptionsFlow,
)
from custom_components.tion.const import (
    CONF_BREEZER_GUID,
    CONF_CAPTCHA_TOKEN,
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_DEVICE_KEY,
    CONF_DEVICE_KEY_ID,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_INTERVAL,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    CONF_PID_MAX_SPEED,
    CONF_PID_MIN_SPEED,
    CONF_PID_TARGET_CO2,
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    CONF_PRESETS,
    DOMAIN,
    SUPPORTED_PRESETS,
    TionPresetType,
)
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_CODE, CONF_EMAIL, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import HEADER_FRONTEND_BASE
from homeassistant.helpers.http import current_request
from homeassistant.helpers.typing import UNDEFINED

from .api.payloads import PROFILE_4S  # noqa: TID251
from .common import setup_entry  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BREEZER_3S,
    BREEZER_4S,
    BREEZER_O2,
    MAGICAIR,
    FakeAuth as CloudAuth,
    FakeTionCloud,
    default_account,
    dps,
    set_values,
)

BREEZER_GUID = BREEZER_4S
SECOND_BREEZER_GUID = BREEZER_3S
SENSOR_ENTITY_ID = "sensor.external_co2"


def _preset_options() -> dict[str, Any]:
    """Return fake preset options for one breezer."""
    return {
        CONF_PRESETS: {
            BREEZER_GUID: {
                "boost": {
                    CONF_PRESET_TYPE: TionPresetType.LOCAL_PID.value,
                    CONF_PRESET_MIN_SPEED: 4,
                    CONF_PRESET_MAX_SPEED: 6,
                },
            }
        }
    }


def _pid_options(*, enabled: bool = True) -> dict[str, Any]:
    """Return fake local PID options."""
    return {
        CONF_PID_BREEZERS: {
            BREEZER_GUID: {
                CONF_PID_ENABLED: enabled,
                CONF_CO2_SENSOR_ENTITY_ID: SENSOR_ENTITY_ID,
                CONF_PID_BASE_OUTPUT: 20.0,
                CONF_PID_KP: 0.5,
                CONF_PID_KI: 0.002,
                CONF_PID_KD: 0.0,
            },
            SECOND_BREEZER_GUID: {
                CONF_PID_ENABLED: enabled,
                CONF_CO2_SENSOR_ENTITY_ID: "sensor.second_co2",
                CONF_PID_BASE_OUTPUT: 15.0,
                CONF_PID_KP: 0.4,
                CONF_PID_KI: 0.001,
                CONF_PID_KD: 0.0,
            },
        },
    }


def _flow(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    options: dict[str, Any] | None = None,
) -> TionOptionsFlow:
    """Return an options flow of the entry, driven step by step."""
    if options is not None:
        hass.config_entries.async_update_entry(entry, options=options)
    flow = TionOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


def _pid_form_input(
    *,
    enabled: bool = True,
    sensor_entity_id: str | None = SENSOR_ENTITY_ID,
) -> dict[str, Any]:
    """Return a fake PID form input."""
    return {
        CONF_PID_ENABLED: enabled,
        CONF_CO2_SENSOR_ENTITY_ID: sensor_entity_id,
        CONF_PID_INTERVAL: 45,
        CONF_PID_BASE_OUTPUT: 20.0,
        CONF_PID_KP: 0.5,
        CONF_PID_KI: 0.002,
        CONF_PID_KD: 0.0,
    }


def _select_options(result: dict[str, Any], field: str) -> list[str]:
    """Return the choices of a select field of a shown form."""
    fields = {str(key): value for key, value in result["data_schema"].schema.items()}
    return list(fields[field].config["options"])


def _single_breezer_account() -> TionAccount:
    """Return the default account with the 4S breezer and its MagicAir station."""
    account = default_account()
    (location,) = account.locations
    kept = tuple(d for d in location.devices if d.id in (BREEZER_4S, MAGICAIR))
    return replace(account, locations=(replace(location, devices=kept),))


def _all_presets() -> dict[str, Any]:
    """Return options with every supported preset name taken."""
    return {
        CONF_PRESETS: {
            BREEZER_GUID: {
                name: {
                    CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
                    CONF_PRESET_SPEED: 2,
                }
                for name in SUPPORTED_PRESETS
            }
        }
    }


async def test_options_init_done_saves_existing_options(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test Done keeps the existing options."""
    flow = _flow(hass, init_integration, _pid_options())

    result = await flow.async_step_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PID_BREEZERS] == _pid_options()[CONF_PID_BREEZERS]


async def test_options_pid_form_saves_draft_and_returns_to_local_pid_menu(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Saving the PID form stores the draft and returns to the breezer's menu."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_breezer(_pid_form_input(enabled=False))

    pid_options = flow._options[CONF_PID_BREEZERS][BREEZER_GUID]  # noqa: SLF001
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "local_pid_menu"
    assert pid_options[CONF_PID_ENABLED] is False
    assert pid_options[CONF_CO2_SENSOR_ENTITY_ID] == SENSOR_ENTITY_ID
    assert pid_options[CONF_PID_INTERVAL] == 45


async def test_options_pid_form_defaults_interval(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A breezer without PID settings gets the default PID interval."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_breezer()

    assert result["data_schema"]({})[CONF_PID_INTERVAL] == 60


@pytest.mark.parametrize(
    "interval", [pytest.param(9, id="below"), pytest.param(601, id="above")]
)
async def test_options_pid_form_rejects_interval_out_of_range(
    hass: HomeAssistant, init_integration: MockConfigEntry, interval: int
) -> None:
    """The PID interval stays within 10 to 600 seconds."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_breezer()

    with pytest.raises(vol.Invalid):
        result["data_schema"]({CONF_PID_INTERVAL: interval})


@pytest.mark.parametrize(
    "interval", [pytest.param(10, id="lowest"), pytest.param(600, id="highest")]
)
async def test_options_pid_form_accepts_interval_bounds(
    hass: HomeAssistant, init_integration: MockConfigEntry, interval: int
) -> None:
    """Both ends of the PID interval range are accepted."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_breezer()

    assert (
        result["data_schema"]({CONF_PID_INTERVAL: interval})[CONF_PID_INTERVAL]
        == interval
    )


def _pid_options_with_entity_settings(min_speed: int) -> dict[str, Any]:
    """Return PID options with the values the number entities edit."""
    options = _pid_options()
    options[CONF_PID_BREEZERS][BREEZER_GUID] |= {
        CONF_PID_MIN_SPEED: min_speed,
        CONF_PID_MAX_SPEED: 4,
        CONF_PID_TARGET_CO2: 700,
    }
    return options


async def test_options_pid_form_keeps_entity_settings(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Saving the PID form keeps the limits and target the entities set."""
    flow = _flow(hass, init_integration, _pid_options_with_entity_settings(2))
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    await flow.async_step_breezer(_pid_form_input())

    pid_options = flow._options[CONF_PID_BREEZERS][BREEZER_GUID]  # noqa: SLF001
    assert pid_options[CONF_PID_MIN_SPEED] == 2
    assert pid_options[CONF_PID_MAX_SPEED] == 4
    assert pid_options[CONF_PID_TARGET_CO2] == 700


async def test_options_save_takes_entity_settings_changed_meanwhile(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A limit moved while the flow is open is not undone by saving."""
    flow = _flow(hass, init_integration, _pid_options())
    draft = flow._options  # noqa: SLF001
    hass.config_entries.async_update_entry(
        init_integration, options=_pid_options_with_entity_settings(3)
    )

    result = await flow.async_step_done()

    assert CONF_PID_MIN_SPEED not in draft[CONF_PID_BREEZERS][BREEZER_GUID]
    assert result["data"][CONF_PID_BREEZERS][BREEZER_GUID][CONF_PID_MIN_SPEED] == 3


async def test_options_pid_removed_in_session_starts_fresh(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Removing and setting up PID again in one session drops the old values."""
    flow = _flow(hass, init_integration, _pid_options_with_entity_settings(3))
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    await flow.async_step_pid_remove()
    await flow.async_step_breezer(_pid_form_input())

    result = await flow.async_step_done()

    assert CONF_PID_MIN_SPEED not in result["data"][CONF_PID_BREEZERS][BREEZER_GUID]


async def test_options_local_pid_remove_selected_breezer_only(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test Remove Local PID deletes only the selected breezer draft options."""
    flow = _flow(hass, init_integration, _pid_options())

    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_pid_remove()

    pid_breezers = flow._options[CONF_PID_BREEZERS]  # noqa: SLF001
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "local_pid_menu"
    assert result["menu_options"] == ["breezer", "init"]
    assert BREEZER_GUID not in pid_breezers
    assert SECOND_BREEZER_GUID in pid_breezers


async def test_options_local_pid_remove_last_breezer_clears_pid_breezers(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test removing the last Local PID entry clears the pid_breezers key."""
    flow = _flow(
        hass,
        init_integration,
        {
            CONF_PID_BREEZERS: {
                BREEZER_GUID: _pid_options()[CONF_PID_BREEZERS][BREEZER_GUID],
            }
        },
    )

    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_pid_remove()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "local_pid_menu"
    assert CONF_PID_BREEZERS not in flow._options  # noqa: SLF001


async def test_options_preset_config_rejects_min_above_max(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test PID preset config validates min_speed <= max_speed."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001
    flow._preset_type = TionPresetType.LOCAL_PID.value  # noqa: SLF001

    result = await flow.async_step_preset_config(
        {CONF_PRESET_MIN_SPEED: 5, CONF_PRESET_MAX_SPEED: 2}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert result["errors"]["base"] == "min_above_max"


async def test_options_preset_config_saves_pid_preset(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test a valid PID preset config is stored with its type."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001
    flow._preset_type = TionPresetType.LOCAL_PID.value  # noqa: SLF001

    result = await flow.async_step_preset_config(
        {CONF_PRESET_MIN_SPEED: 4, CONF_PRESET_MAX_SPEED: 6}
    )

    stored = flow._options[CONF_PRESETS][BREEZER_GUID]["boost"]  # noqa: SLF001
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "presets_menu"
    assert stored == {
        CONF_PRESET_TYPE: TionPresetType.LOCAL_PID.value,
        CONF_PRESET_MIN_SPEED: 4,
        CONF_PRESET_MAX_SPEED: 6,
    }


async def test_options_pid_preset_defaults(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A new PID preset starts at speed 1 up to the breezer's top speed."""
    flow = _flow(hass, init_integration, _pid_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "eco"  # noqa: SLF001
    flow._preset_type = TionPresetType.LOCAL_PID.value  # noqa: SLF001

    result = await flow.async_step_preset_config()

    assert result["data_schema"]({}) == {
        CONF_PRESET_MIN_SPEED: 1,
        CONF_PRESET_MAX_SPEED: 6,
    }


@pytest.mark.parametrize(
    ("options", "types"),
    [
        pytest.param({}, ["manual"], id="without_pid"),
        pytest.param(_pid_options(enabled=False), ["manual"], id="pid_disabled"),
        pytest.param(_pid_options(), ["manual", "local_pid"], id="with_pid"),
    ],
)
async def test_options_preset_types_follow_draft_pid(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    options: dict[str, Any],
    types: list[str],
) -> None:
    """PID presets are offered only while the draft sets up PID."""
    flow = _flow(hass, init_integration, options)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_preset_add()

    assert _select_options(result, CONF_PRESET_TYPE) == types


async def test_options_ignore_presets_of_old_types(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Old auto presets are not listed, and their name can be taken."""
    old_auto = {
        CONF_PRESET_TYPE: "auto",
        CONF_PRESET_MIN_SPEED: 1,
        CONF_PRESET_MAX_SPEED: 3,
    }
    manual = {CONF_PRESET_TYPE: TionPresetType.MANUAL.value, CONF_PRESET_SPEED: 5}
    flow = _flow(
        hass,
        init_integration,
        {CONF_PRESETS: {BREEZER_GUID: {"eco": old_auto, "boost": manual}}},
    )
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    edit = await flow.async_step_preset_edit()
    add = await flow.async_step_preset_add()
    await flow.async_step_preset_add(
        {CONF_PRESET_NAME: "eco", CONF_PRESET_TYPE: TionPresetType.MANUAL.value}
    )
    await flow.async_step_preset_config({CONF_PRESET_SPEED: 2})

    assert _select_options(edit, CONF_PRESET_NAME) == ["boost"]
    assert "eco" in _select_options(add, CONF_PRESET_NAME)
    assert flow._options[CONF_PRESETS][BREEZER_GUID] == {  # noqa: SLF001
        "eco": {CONF_PRESET_TYPE: TionPresetType.MANUAL.value, CONF_PRESET_SPEED: 2},
        "boost": manual,
    }


async def test_options_preset_config_saves_manual_preset(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test a valid manual preset config is stored with its target speed."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001
    flow._preset_type = TionPresetType.MANUAL.value  # noqa: SLF001

    result = await flow.async_step_preset_config({CONF_PRESET_SPEED: 5})

    stored = flow._options[CONF_PRESETS][BREEZER_GUID]["boost"]  # noqa: SLF001
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "presets_menu"
    assert stored == {
        CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
        CONF_PRESET_SPEED: 5,
    }


async def test_options_preset_add_selects_name_and_type_opens_config(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test selecting a preset name and type opens the config form."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_preset_add(
        {
            CONF_PRESET_NAME: "boost",
            CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
        }
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert flow._preset_name == "boost"  # noqa: SLF001
    assert flow._preset_type == TionPresetType.MANUAL.value  # noqa: SLF001


async def test_options_preset_remove_deletes_and_cleans_up(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test removing the only preset clears the breezer and CONF_PRESETS keys."""
    flow = _flow(hass, init_integration, _preset_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_preset_remove({CONF_PRESET_NAME: "boost"})

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "presets_menu"
    assert CONF_PRESETS not in flow._options  # noqa: SLF001


async def test_options_preset_edit_selects_and_opens_prefilled_config(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test selecting a preset to edit opens preset_config (exercising pre-fill)."""
    flow = _flow(hass, init_integration, _preset_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_preset_edit({CONF_PRESET_NAME: "boost"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert flow._preset_name == "boost"  # noqa: SLF001
    assert flow._preset_type == TionPresetType.LOCAL_PID.value  # noqa: SLF001


async def test_options_preset_add_all_taken_returns_to_presets_menu(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Add when every preset name is taken returns to the presets menu."""
    flow = _flow(hass, init_integration, _all_presets())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_preset_add()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "presets_menu"


async def test_options_final_done_saves_draft_changes(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test final Done saves accumulated draft changes."""
    flow = _flow(hass, init_integration, _pid_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    await flow.async_step_pid_remove()
    await flow.async_step_breezer(_pid_form_input(sensor_entity_id="sensor.new"))

    result = await flow.async_step_done()

    pid_options = result["data"][CONF_PID_BREEZERS][BREEZER_GUID]
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert pid_options[CONF_CO2_SENSOR_ENTITY_ID] == "sensor.new"
    assert SECOND_BREEZER_GUID in result["data"][CONF_PID_BREEZERS]


@pytest.mark.parametrize(
    ("action", "step_id"),
    [
        pytest.param("configure_local_pid", "local_pid", id="local_pid"),
        pytest.param("configure_presets", "presets", id="presets"),
    ],
)
async def test_options_init_asks_which_breezer(
    hass: HomeAssistant, init_integration: MockConfigEntry, action: str, step_id: str
) -> None:
    """With several breezers the menu items ask which one first."""
    flow = _flow(hass, init_integration)

    result = await getattr(flow, f"async_step_{action}")()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step_id
    assert _select_options(result, CONF_BREEZER_GUID) == [
        {"value": BREEZER_4S, "label": "Breezer 4S"},
        {"value": BREEZER_3S, "label": "Breezer 3S"},
        {"value": BREEZER_O2, "label": "Breezer O2"},
    ]


@pytest.mark.parametrize("account", [pytest.param(_single_breezer_account(), id="one")])
@pytest.mark.parametrize(
    ("action", "step_id", "placeholders"),
    [
        pytest.param(
            "configure_local_pid",
            "local_pid_menu",
            {"breezer": "Breezer 4S", "sensor": "—"},
            id="local_pid",
        ),
        pytest.param(
            "configure_presets",
            "presets_menu",
            {"breezer": "Breezer 4S", "count": "0"},
            id="presets",
        ),
    ],
)
async def test_options_init_skips_breezer_choice_for_one_breezer(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    action: str,
    step_id: str,
    placeholders: dict[str, str],
) -> None:
    """A single breezer goes straight to its menu."""
    flow = _flow(hass, init_integration)

    result = await getattr(flow, f"async_step_{action}")()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == step_id
    assert result["description_placeholders"] == placeholders
    assert flow._breezer_guid == BREEZER_GUID  # noqa: SLF001


async def test_options_init_shows_main_menu(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The options flow opens with the main menu."""
    flow = _flow(hass, init_integration)

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == [
        "configure_local_pid",
        "configure_presets",
        "done",
    ]


async def test_options_init_without_breezers_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """An entry that is not loaded cannot offer breezers."""
    flow = _flow(hass, config_entry)

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_breezers"


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("configure_local_pid", id="local_pid"),
        pytest.param("configure_presets", id="presets"),
    ],
)
async def test_options_menu_item_without_breezers_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry, action: str
) -> None:
    """A menu item aborts when the entry unloaded in the middle of the flow."""
    flow = _flow(hass, config_entry)

    result = await getattr(flow, f"async_step_{action}")()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_breezers"


@pytest.mark.parametrize(
    ("step", "menu_step"),
    [
        pytest.param("local_pid", "local_pid_menu", id="local_pid"),
        pytest.param("presets", "presets_menu", id="presets"),
    ],
)
async def test_options_breezer_choice_opens_its_menu(
    hass: HomeAssistant, init_integration: MockConfigEntry, step: str, menu_step: str
) -> None:
    """Choosing a breezer opens the menu of that breezer."""
    flow = _flow(hass, init_integration)

    result = await getattr(flow, f"async_step_{step}")(
        {CONF_BREEZER_GUID: SECOND_BREEZER_GUID}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == menu_step
    assert result["description_placeholders"]["breezer"] == "Breezer 3S"
    assert flow._breezer_guid == SECOND_BREEZER_GUID  # noqa: SLF001


@pytest.mark.parametrize(
    ("options", "menu_options", "sensor"),
    [
        pytest.param({}, ["breezer", "init"], "—", id="without_pid"),
        pytest.param(
            _pid_options(enabled=False), ["breezer", "init"], "—", id="pid_disabled"
        ),
        pytest.param(
            _pid_options(),
            ["breezer", "pid_remove", "init"],
            SENSOR_ENTITY_ID,
            id="with_pid",
        ),
    ],
)
async def test_options_local_pid_menu_offers_what_applies(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    options: dict[str, Any],
    menu_options: list[str],
    sensor: str,
) -> None:
    """Removing PID is offered only while PID is set up; the sensor is shown."""
    flow = _flow(hass, init_integration, options)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_local_pid_menu()

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == menu_options
    assert result["description_placeholders"] == {
        "breezer": "Breezer 4S",
        "sensor": sensor,
    }


async def test_options_local_pid_menu_names_the_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The menu shows the sensor's friendly name when it has a state."""
    hass.states.async_set(SENSOR_ENTITY_ID, "700", {"friendly_name": "Living room CO2"})
    flow = _flow(hass, init_integration, _pid_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_local_pid_menu()

    assert result["description_placeholders"]["sensor"] == "Living room CO2"


async def test_options_local_pid_menu_shows_the_chosen_breezer(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """PID set up for one breezer does not show in another breezer's menu."""
    options = {
        CONF_PID_BREEZERS: {
            BREEZER_GUID: _pid_options()[CONF_PID_BREEZERS][BREEZER_GUID]
        }
    }
    flow = _flow(hass, init_integration, options)

    result = await flow.async_step_local_pid({CONF_BREEZER_GUID: SECOND_BREEZER_GUID})

    assert result["menu_options"] == ["breezer", "init"]
    assert result["description_placeholders"] == {
        "breezer": "Breezer 3S",
        "sensor": "—",
    }


async def test_options_pid_remove_shows_menu_without_pid(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """After removing PID the menu shows no sensor and no remove action."""
    flow = _flow(hass, init_integration, _pid_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_pid_remove()

    assert result["menu_options"] == ["breezer", "init"]
    assert result["description_placeholders"]["sensor"] == "—"


async def test_options_pid_form_opens_with_current_values(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The PID form of a breezer starts from its stored values."""
    flow = _flow(hass, init_integration, _pid_options())
    flow._breezer_guid = SECOND_BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_breezer()

    assert result["step_id"] == "breezer"
    assert result["data_schema"]({})[CONF_PID_KP] == 0.4


async def test_options_pid_form_requires_sensor_when_enabled(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An enabled PID needs a CO2 sensor."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_breezer(_pid_form_input(sensor_entity_id=None))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CO2_SENSOR_ENTITY_ID: "required"}


@pytest.mark.parametrize(
    ("options", "menu_options", "count"),
    [
        pytest.param({}, ["preset_add", "init"], "0", id="none"),
        pytest.param(
            _preset_options(),
            ["preset_add", "preset_edit", "preset_remove", "init"],
            "1",
            id="some",
        ),
        pytest.param(
            _all_presets(),
            ["preset_edit", "preset_remove", "init"],
            str(len(SUPPORTED_PRESETS)),
            id="all_names_taken",
        ),
        pytest.param(
            {CONF_PRESETS: {BREEZER_GUID: {"eco": {CONF_PRESET_TYPE: "auto"}}}},
            ["preset_add", "init"],
            "0",
            id="only_old_auto",
        ),
    ],
)
async def test_options_presets_menu_offers_what_applies(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    options: dict[str, Any],
    menu_options: list[str],
    count: str,
) -> None:
    """The presets menu lists only the actions the breezer can take."""
    flow = _flow(hass, init_integration, options)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await flow.async_step_presets_menu()

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == menu_options
    assert result["description_placeholders"] == {
        "breezer": "Breezer 4S",
        "count": count,
    }


@pytest.mark.parametrize(
    "step",
    [
        pytest.param("preset_edit", id="edit"),
        pytest.param("preset_remove", id="remove"),
    ],
)
async def test_options_preset_steps_without_presets_return_to_menu(
    hass: HomeAssistant, init_integration: MockConfigEntry, step: str
) -> None:
    """Editing or removing with nothing configured goes back to the menu."""
    flow = _flow(hass, init_integration)
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = await getattr(flow, f"async_step_{step}")()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "presets_menu"


async def test_options_init_menu_option_shows_init_keeping_draft(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Back from a menu shows the main menu; the draft is kept until Save."""
    flow = _flow(hass, init_integration, _pid_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    await flow.async_step_pid_remove()

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert BREEZER_GUID not in flow._options[CONF_PID_BREEZERS]  # noqa: SLF001


async def test_options_flow_offered(config_entry: MockConfigEntry) -> None:
    """The entry offers the options flow."""
    assert TionConfigFlow.async_supports_options_flow(config_entry)


@pytest.mark.parametrize(
    ("steps", "reloads", "pid_breezers"),
    [
        pytest.param(
            [
                {"next_step_id": "configure_local_pid"},
                {CONF_BREEZER_GUID: BREEZER_GUID},
                {"next_step_id": "pid_remove"},
                {"next_step_id": "init"},
                {"next_step_id": "done"},
            ],
            1,
            {SECOND_BREEZER_GUID},
            id="changed",
        ),
        pytest.param(
            [{"next_step_id": "done"}],
            0,
            {BREEZER_GUID, SECOND_BREEZER_GUID},
            id="unchanged",
        ),
    ],
)
async def test_options_save_reloads_changed_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: CloudAuth,
    steps: list[dict[str, Any]],
    reloads: int,
    pid_breezers: set[str],
) -> None:
    """Saving changed options reloads the entry; saving unchanged ones does not."""
    hass.config_entries.async_update_entry(config_entry, options=_pid_options())
    await setup_entry(hass, config_entry, cloud, auth)
    create_cloud = AsyncMock(return_value=(auth, cloud))

    with patch("custom_components.tion.async_create_cloud", create_cloud):
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        for user_input in steps:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], user_input
            )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert create_cloud.call_count == reloads
    assert set(config_entry.options[CONF_PID_BREEZERS]) == pid_breezers


async def test_options_list_the_account_breezers(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The breezer selectors offer the account's breezers by device id."""
    flow = _flow(hass, init_integration)

    assert flow._breezer_options() == [  # noqa: SLF001
        {"value": BREEZER_4S, "label": "Breezer 4S"},
        {"value": BREEZER_3S, "label": "Breezer 3S"},
        {"value": BREEZER_O2, "label": "Breezer O2"},
    ]


async def test_options_without_loaded_entry_list_no_breezers(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """An entry that failed to load offers no breezers."""
    assert _flow(hass, config_entry)._breezer_options() == []  # noqa: SLF001


@pytest.mark.parametrize(
    ("account", "breezer", "max_speed"),
    [
        pytest.param(
            set_values(
                default_account(), BREEZER_4S, dps(PROFILE_4S, fan_speed_maxavail=5)
            ),
            BREEZER_4S,
            5,
            id="reported",
        ),
        pytest.param(default_account(), BREEZER_O2, 4, id="model_table"),
        pytest.param(default_account(), "UNKNOWN001", 6, id="unknown"),
    ],
)
async def test_options_preset_speed_limit(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    breezer: str,
    max_speed: int,
) -> None:
    """Preset speeds are limited by the breezer's top speed."""
    flow = _flow(hass, init_integration)

    assert flow._breezer_max_speed(breezer) == max_speed  # noqa: SLF001


FLOW_ID = "0123456789abcdef0123456789abcdef"
EMAIL = "user@example.com"
EMAIL_UNIQUE_ID = hashlib.sha256(EMAIL.encode()).hexdigest()
REAUTH_ENTRY_ID = "reauth-entry-id"
TOKENS = TionTokens(
    access_token="access",
    renew_session_token="renew",
    access_expires_at=1_790_000_900.0,
    refresh_expires_at=1_792_592_000.0,
)
TRANSLATIONS = Path(__file__).parents[1] / "custom_components/tion/translations"


class FakeAuth:
    """Scripted stand-in for TionAuth used by the config flow."""

    def __init__(self, device_key: TionDeviceKey) -> None:
        """Initialize with the flow's device key."""
        self.device_key = device_key
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.errors: dict[str, list[Exception]] = {}

    def fail(self, method: str, *errors: Exception) -> None:
        """Make the next calls of method raise the given errors."""
        self.errors[method] = list(errors)

    def _record(self, method: str, *args: str) -> None:
        self.calls.append((method, args))
        if self.errors.get(method):
            raise self.errors[method].pop(0)

    async def async_get_confirmation_code(
        self, email: str, password: str, captcha_token: str
    ) -> None:
        """Record step 1."""
        self._record("get_confirmation_code", email, password, captcha_token)

    async def async_check_confirmation_code(self, code: str) -> None:
        """Record step 2."""
        self._record("check_confirmation_code", code)

    async def async_get_token(self) -> TionTokens:
        """Record step 3 and return tokens."""
        self._record("get_token")
        return TOKENS


class FakeFlowConfigEntries:
    """The parts of hass.config_entries that ConfigFlow helpers use."""

    def __init__(self, entries: list[SimpleNamespace]) -> None:
        """Initialize with existing entries."""
        self.entries = entries
        self.reloaded: list[str] = []
        self.progress: list[dict[str, Any]] = []
        self.flow = SimpleNamespace(async_progress_by_handler=self._progress_by_handler)

    def _progress_by_handler(
        self,
        handler: str,
        include_uninitialized: bool = False,
        match_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the in-progress flows whose context contains match_context."""
        wanted = (match_context or {}).items()
        return [flow for flow in self.progress if wanted <= flow["context"].items()]

    def async_entry_for_domain_unique_id(
        self, domain: str, unique_id: str
    ) -> SimpleNamespace | None:
        """Return the entry with this unique id."""
        return next((e for e in self.entries if e.unique_id == unique_id), None)

    def async_get_known_entry(self, entry_id: str) -> SimpleNamespace:
        """Return the entry with this id."""
        return next(e for e in self.entries if e.entry_id == entry_id)

    def async_update_entry(self, entry: SimpleNamespace, **changes: Any) -> bool:
        """Apply defined changes."""
        for key, value in changes.items():
            if value is not UNDEFINED:
                setattr(entry, key, value)
        return True

    def async_schedule_reload(self, entry_id: str) -> None:
        """Record a scheduled reload."""
        self.reloaded.append(entry_id)


class FakeFlowHass:
    """The parts of hass the Tion config flow uses."""

    def __init__(self, entries: list[SimpleNamespace] | None = None) -> None:
        """Initialize with existing entries."""
        self.data: dict[str, Any] = {}
        self.config_entries = FakeFlowConfigEntries(entries or [])
        self.registered_views: list[Any] = []
        self.http = SimpleNamespace(register_view=self.registered_views.append)


def _entry(data: dict[str, Any], unique_id: str = EMAIL_UNIQUE_ID) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id=REAUTH_ENTRY_ID,
        unique_id=unique_id,
        title=EMAIL,
        data=data,
        source=SOURCE_USER,
        update_listeners=[],
    )


@pytest.fixture
def auths(monkeypatch: pytest.MonkeyPatch) -> list[FakeAuth]:
    """Replace session creation; collect every FakeAuth the flow creates."""
    created: list[FakeAuth] = []

    async def create(hass: Any, device_key: TionDeviceKey) -> FakeAuth:
        created.append(FakeAuth(device_key))
        return created[-1]

    monkeypatch.setattr(config_flow, "async_create_auth", create)
    return created


def _config_flow(
    hass: FakeFlowHass, source: str = SOURCE_USER, entry: SimpleNamespace | None = None
) -> TionConfigFlow:
    flow = TionConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = FLOW_ID
    flow.context = {"source": source}
    if entry is not None:
        flow.context |= {"entry_id": entry.entry_id, "unique_id": entry.unique_id}
    return flow


async def _to_code_step(flow: TionConfigFlow) -> None:
    await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})
    await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})


@pytest.mark.asyncio
async def test_user_step_shows_form() -> None:
    """The flow starts with an e-mail and password form."""
    result = await _config_flow(FakeFlowHass()).async_step_user()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    assert set(result["data_schema"].schema) == {CONF_EMAIL, CONF_PASSWORD}


@pytest.mark.asyncio
async def test_user_submit_opens_captcha_page(auths: list[FakeAuth]) -> None:
    """Credentials lead to the captcha page for this flow, on the frontend's origin."""
    hass = FakeFlowHass()
    flow = _config_flow(hass)
    request = SimpleNamespace(headers={HEADER_FRONTEND_BASE: "http://localhost:8123"})

    token = current_request.set(request)
    try:
        result = await flow.async_step_user(
            {CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"}
        )
    finally:
        current_request.reset(token)

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "captcha"
    assert result["url"] == f"http://localhost:8123/api/tion/captcha/{FLOW_ID}"
    assert flow.unique_id == EMAIL_UNIQUE_ID
    assert len(hass.registered_views) == 1
    assert len(auths) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("auths")
async def test_user_submit_aborts_when_account_configured() -> None:
    """The same account cannot be added twice."""
    flow = _config_flow(FakeFlowHass([_entry({CONF_EMAIL: EMAIL})]))

    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})

    assert exc_info.value.reason == "already_configured"


@pytest.mark.asyncio
async def test_user_submit_ignores_stranded_flow_for_same_account(
    auths: list[FakeAuth],
) -> None:
    """A user flow that can no longer be resumed must not block a new attempt."""
    hass = FakeFlowHass()
    hass.config_entries.progress.append(
        {
            "flow_id": "stranded-flow-id",
            "context": {"source": SOURCE_USER, "unique_id": EMAIL_UNIQUE_ID},
        }
    )
    flow = _config_flow(hass)

    result = await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "captcha"
    assert len(auths) == 1


@pytest.mark.asyncio
async def test_captcha_token_requests_code(auths: list[FakeAuth]) -> None:
    """The captcha token is used at once and the flow moves to the code form."""
    flow = _config_flow(FakeFlowHass())
    await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})

    result = await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})

    assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
    assert result["step_id"] == "code"
    assert auths[0].calls == [("get_confirmation_code", (EMAIL, "secret", "dD0xtoken"))]


@pytest.mark.parametrize(
    "errors",
    [
        pytest.param((), id="success"),
        pytest.param((TionLoginError(LOGIN_ERROR_INVALID_AUTH),), id="failure"),
    ],
)
@pytest.mark.asyncio
async def test_captcha_phase_drops_the_password(
    errors: tuple[Exception, ...], auths: list[FakeAuth]
) -> None:
    """The password is only needed for the code request and is not kept."""
    flow = _config_flow(FakeFlowHass())
    await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})
    auths[0].fail("get_confirmation_code", *errors)

    await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})

    assert "secret" not in vars(flow).values()
    assert auths[0].calls == [("get_confirmation_code", (EMAIL, "secret", "dD0xtoken"))]


@pytest.mark.parametrize(
    ("error", "key"),
    [
        pytest.param(
            TionLoginError(LOGIN_ERROR_INVALID_CAPTCHA), "invalid_captcha", id="captcha"
        ),
        pytest.param(
            TionLoginError(LOGIN_ERROR_INVALID_AUTH), "invalid_auth", id="auth"
        ),
        pytest.param(TionConnectionError("down"), "cannot_connect", id="network"),
        pytest.param(TionApiError("weird"), "unknown", id="api"),
        pytest.param(RuntimeError("bug"), "unknown", id="unexpected"),
    ],
)
@pytest.mark.asyncio
async def test_captcha_failure_returns_to_user_form_with_error(
    error: Exception, key: str, auths: list[FakeAuth]
) -> None:
    """An external step cannot show errors: they reappear on the user form."""
    flow = _config_flow(FakeFlowHass())
    await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})
    auths[0].fail("get_confirmation_code", error)

    done = await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})
    form = await flow.async_step_user()
    again = await flow.async_step_user()

    assert done["type"] is FlowResultType.EXTERNAL_STEP_DONE
    assert done["step_id"] == "user"
    assert form["errors"] == {"base": key}
    assert again["errors"] == {}
    email_key = next(k for k in form["data_schema"].schema if k == CONF_EMAIL)
    assert email_key.description == {"suggested_value": EMAIL}


@pytest.mark.asyncio
async def test_retry_after_captcha_failure_uses_new_session(
    auths: list[FakeAuth],
) -> None:
    """Resubmitting credentials starts over with a fresh device key."""
    flow = _config_flow(FakeFlowHass())
    await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})
    auths[0].fail("get_confirmation_code", TionLoginError(LOGIN_ERROR_INVALID_CAPTCHA))
    await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "expired"})

    result = await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert len(auths) == 2
    assert auths[0].device_key.key_id() != auths[1].device_key.key_id()


@pytest.mark.asyncio
async def test_code_step_creates_entry_without_password(
    auths: list[FakeAuth],
) -> None:
    """The entry stores e-mail, device key and tokens, never the password."""
    flow = _config_flow(FakeFlowHass())
    await _to_code_step(flow)

    form = await flow.async_step_code()
    result = await flow.async_step_code({CONF_CODE: "6483"})

    assert form["step_id"] == "code"
    assert form["description_placeholders"] == {"email": EMAIL}
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    device_key = auths[0].device_key
    assert result["data"] == {
        CONF_EMAIL: EMAIL,
        CONF_DEVICE_KEY: device_key.to_pem(),
        CONF_DEVICE_KEY_ID: device_key.key_id(),
        **TOKENS.as_dict(),
    }
    assert TionDeviceKey.from_pem(result["data"][CONF_DEVICE_KEY]).key_id() == (
        device_key.key_id()
    )
    assert auths[0].calls[1:] == [
        ("check_confirmation_code", ("6483",)),
        ("get_token", ()),
    ]


@pytest.mark.parametrize(
    ("error", "key"),
    [
        pytest.param(
            TionLoginError(LOGIN_ERROR_INVALID_CODE), "invalid_code", id="wrong_code"
        ),
        pytest.param(TionConnectionError("down"), "cannot_connect", id="network"),
        pytest.param(RuntimeError("bug"), "unknown", id="unexpected"),
    ],
)
@pytest.mark.asyncio
async def test_code_step_error_allows_retry(
    error: Exception, key: str, auths: list[FakeAuth]
) -> None:
    """A wrong code or a network error keeps the user on the code form."""
    flow = _config_flow(FakeFlowHass())
    await _to_code_step(flow)
    auths[0].fail("check_confirmation_code", error)

    failed = await flow.async_step_code({CONF_CODE: "1111"})
    result = await flow.async_step_code({CONF_CODE: "6483"})

    assert failed["type"] is FlowResultType.FORM
    assert failed["step_id"] == "code"
    assert failed["errors"] == {"base": key}
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_code_step_aborts_when_account_configured_meanwhile(
    auths: list[FakeAuth],
) -> None:
    """Another flow finishing first leaves no room for a duplicate entry."""
    hass = FakeFlowHass()
    flow = _config_flow(hass)
    await _to_code_step(flow)
    hass.config_entries.entries.append(_entry({CONF_EMAIL: EMAIL}))

    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_code({CONF_CODE: "6483"})

    assert exc_info.value.reason == "already_configured"
    assert [call[0] for call in auths[0].calls] == [
        "get_confirmation_code",
        "check_confirmation_code",
        "get_token",
    ]


@pytest.mark.asyncio
async def test_code_expired_returns_to_user_form(auths: list[FakeAuth]) -> None:
    """An expired e-mail token needs a new captcha, so the flow starts over."""
    flow = _config_flow(FakeFlowHass())
    await _to_code_step(flow)
    auths[0].fail("get_token", TionLoginError(LOGIN_ERROR_CODE_EXPIRED))

    result = await flow.async_step_code({CONF_CODE: "6483"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "code_expired"}


@pytest.mark.asyncio
async def test_password_token_aborts(auths: list[FakeAuth]) -> None:
    """Accounts without a permanent password cannot log in from HA."""
    flow = _config_flow(FakeFlowHass())
    await _to_code_step(flow)
    auths[0].fail(
        "check_confirmation_code", TionLoginError(LOGIN_ERROR_PASSWORD_NOT_SET)
    )

    result = await flow.async_step_code({CONF_CODE: "6483"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "password_not_set"


@pytest.mark.parametrize(
    "entry_data",
    [
        pytest.param({CONF_EMAIL: EMAIL, CONF_DEVICE_KEY: "old"}, id="v4_entry"),
        pytest.param(
            {CONF_USERNAME: EMAIL, CONF_PASSWORD: "old", "auth": {"api": "t"}},
            id="legacy_entry",
        ),
    ],
)
@pytest.mark.asyncio
async def test_reauth_replaces_entry_data(
    entry_data: dict[str, Any], auths: list[FakeAuth]
) -> None:
    """Reauth logs in again with the stored e-mail and rewrites the data."""
    entry = _entry(entry_data)
    hass = FakeFlowHass([entry])
    flow = _config_flow(hass, SOURCE_REAUTH, entry)

    confirm = await flow.async_step_reauth(entry_data)
    external = await flow.async_step_reauth_confirm({CONF_PASSWORD: "new"})
    await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})
    result = await flow.async_step_code({CONF_CODE: "6483"})

    assert confirm["step_id"] == "reauth_confirm"
    assert confirm["description_placeholders"]["email"] == EMAIL
    assert set(confirm["data_schema"].schema) == {CONF_PASSWORD}
    assert external["type"] is FlowResultType.EXTERNAL_STEP
    assert auths[0].calls[0] == ("get_confirmation_code", (EMAIL, "new", "dD0xtoken"))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {
        CONF_EMAIL: EMAIL,
        CONF_DEVICE_KEY: auths[0].device_key.to_pem(),
        CONF_DEVICE_KEY_ID: auths[0].device_key.key_id(),
        **TOKENS.as_dict(),
    }
    assert hass.config_entries.reloaded == [REAUTH_ENTRY_ID]


@pytest.mark.asyncio
async def test_reauth_captcha_failure_returns_to_reauth_form(
    auths: list[FakeAuth],
) -> None:
    """Reauth errors reappear on the reauth form, not the user form."""
    entry = _entry({CONF_EMAIL: EMAIL})
    flow = _config_flow(FakeFlowHass([entry]), SOURCE_REAUTH, entry)
    await flow.async_step_reauth(entry.data)
    await flow.async_step_reauth_confirm({CONF_PASSWORD: "wrong"})
    auths[0].fail("get_confirmation_code", TionLoginError(LOGIN_ERROR_INVALID_AUTH))

    done = await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})
    form = await flow.async_step_reauth_confirm()

    assert done["step_id"] == "reauth_confirm"
    assert form["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("auths")
async def test_reauth_aborts_on_account_mismatch() -> None:
    """An entry whose unique id is not this e-mail's is not overwritten."""
    entry = _entry({CONF_EMAIL: EMAIL}, unique_id="someone-else")
    flow = _config_flow(FakeFlowHass([entry]), SOURCE_REAUTH, entry)
    await flow.async_step_reauth(entry.data)
    await flow.async_step_reauth_confirm({CONF_PASSWORD: "secret"})
    await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})

    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_code({CONF_CODE: "6483"})

    assert exc_info.value.reason == "wrong_account"
    assert entry.data == {CONF_EMAIL: EMAIL}


@pytest.mark.parametrize("language", ["en", "ru"])
def test_translations_cover_login_flow(language: str) -> None:
    """Every step, error and abort the login flow can show is translated."""
    config = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))[
        "config"
    ]

    assert {"user", "reauth_confirm", "captcha", "code"} <= set(config["step"])
    assert set(config["step"]["user"]["data"]) == {CONF_EMAIL, CONF_PASSWORD}
    assert set(config["step"]["code"]["data"]) == {CONF_CODE}
    assert {
        "cannot_connect",
        "invalid_auth",
        "invalid_captcha",
        "invalid_code",
        "code_expired",
        "unknown",
    } <= set(config["error"])
    assert {
        "already_configured",
        "already_in_progress",
        "password_not_set",
        "wrong_account",
    } <= set(config["abort"])


@pytest.mark.parametrize("language", ["en", "ru"])
def test_translations_cover_options_menus(language: str) -> None:
    """Menu titles carry no placeholders, and every menu option is translated."""
    steps = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))[
        "options"
    ]["step"]

    menus = {step_id: step for step_id, step in steps.items() if "menu_options" in step}
    assert all("{" not in step["title"] for step in menus.values())
    assert {step_id: set(step["menu_options"]) for step_id, step in menus.items()} == {
        "init": {"configure_local_pid", "configure_presets", "done"},
        "local_pid_menu": {"breezer", "pid_remove", "init"},
        "presets_menu": {"preset_add", "preset_edit", "preset_remove", "init"},
    }


@pytest.mark.parametrize("language", ["en", "ru"])
def test_translations_cover_options_abort(language: str) -> None:
    """The options flow's no-breezers abort is translated."""
    options = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))[
        "options"
    ]

    assert options["abort"]["no_breezers"]
    assert "no_breezers" not in options["error"]
