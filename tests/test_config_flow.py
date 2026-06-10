"""Tests for Tion config and options flows."""

import asyncio
from types import SimpleNamespace
from typing import Any

from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tion.config_flow import (
    CONF_LOCAL_PID_ACTION,
    CONF_OPTIONS_ACTION,
    CONF_PRESET_NAME,
    CONF_PRESETS_ACTION,
    LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID,
    LOCAL_PID_ACTION_DONE,
    LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
    OPTIONS_ACTION_CONFIGURE_LOCAL_PID,
    OPTIONS_ACTION_CONFIGURE_PRESETS,
    OPTIONS_ACTION_DONE,
    PRESETS_ACTION_ADD,
    PRESETS_ACTION_DONE,
    PRESETS_ACTION_EDIT,
    TionOptionsFlow,
)
from custom_components.tion.const import (
    CONF_BREEZER_GUID,
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESETS,
    DOMAIN,
    SUPPORTED_PRESETS,
    TionDeviceType,
)

BREEZER_GUID = "breezer-guid"
SECOND_BREEZER_GUID = "second-breezer-guid"
SENSOR_ENTITY_ID = "sensor.external_co2"
ENTRY_ID = "entry-id"


class FakeConfigEntry:
    """Fake config entry."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        """Initialize fake config entry."""
        self.entry_id = ENTRY_ID
        self.options = options or {}


class FakeCoordinator:
    """Fake Tion coordinator."""

    def __init__(self, devices: list[SimpleNamespace] | None = None) -> None:
        """Initialize fake coordinator."""
        self.data = {}
        self._devices = devices or [
            SimpleNamespace(
                guid=BREEZER_GUID,
                name="Breezer",
                type=TionDeviceType.BREEZER_4S,
            ),
            SimpleNamespace(
                guid=SECOND_BREEZER_GUID,
                name="Second Breezer",
                type=TionDeviceType.BREEZER_4S,
            ),
        ]

    def get_devices(self) -> list[SimpleNamespace]:
        """Return fake devices."""
        return self._devices


def _preset_options() -> dict[str, Any]:
    """Return fake preset options for one breezer."""
    return {
        CONF_PRESETS: {
            BREEZER_GUID: {
                "boost": {CONF_PRESET_MIN_SPEED: 4, CONF_PRESET_MAX_SPEED: 6},
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


def _flow(options: dict[str, Any] | None = None) -> TionOptionsFlow:
    """Return a fake options flow."""
    flow = TionOptionsFlow(FakeConfigEntry(options))
    flow.hass = SimpleNamespace(data={DOMAIN: {ENTRY_ID: FakeCoordinator()}})
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
        CONF_PID_BASE_OUTPUT: 20.0,
        CONF_PID_KP: 0.5,
        CONF_PID_KI: 0.002,
        CONF_PID_KD: 0.0,
    }


def test_options_init_done_saves_scan_interval_and_existing_options() -> None:
    """Test init Done saves scan interval and keeps existing options."""
    flow = _flow(_pid_options())

    result = asyncio.run(
        flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 30,
                CONF_OPTIONS_ACTION: OPTIONS_ACTION_DONE,
            }
        )
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SCAN_INTERVAL] == 30
    assert result["data"][CONF_PID_BREEZERS] == _pid_options()[CONF_PID_BREEZERS]


def test_options_init_configure_local_pid_opens_local_pid_step() -> None:
    """Test init Configure Local PID opens the local PID menu."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 60,
                CONF_OPTIONS_ACTION: OPTIONS_ACTION_CONFIGURE_LOCAL_PID,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_pid"
    assert flow._options[CONF_SCAN_INTERVAL] == 60  # noqa: SLF001


def test_options_local_pid_done_returns_to_init_without_saving() -> None:
    """Test local PID Done returns to init without creating an entry."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_DONE,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


def test_options_local_pid_add_requires_breezer() -> None:
    """Test Add or update Local PID requires a selected breezer."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_pid"
    assert result["errors"] == {CONF_BREEZER_GUID: "required"}


def test_options_local_pid_add_opens_pid_form_with_current_values() -> None:
    """Test Add or update Local PID opens PID form for the selected breezer."""
    flow = _flow(_pid_options())

    result = asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_BREEZER_GUID: SECOND_BREEZER_GUID,
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "breezer"
    assert flow._breezer_guid == SECOND_BREEZER_GUID  # noqa: SLF001
    assert flow._pid_options(SECOND_BREEZER_GUID)[CONF_PID_KP] == 0.4  # noqa: SLF001


def test_options_pid_form_saves_draft_and_returns_to_local_pid() -> None:
    """Test PID form saves draft options and returns to local PID menu."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = asyncio.run(flow.async_step_breezer(_pid_form_input(enabled=False)))

    pid_options = flow._options[CONF_PID_BREEZERS][BREEZER_GUID]  # noqa: SLF001
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_pid"
    assert pid_options[CONF_PID_ENABLED] is False
    assert pid_options[CONF_CO2_SENSOR_ENTITY_ID] == SENSOR_ENTITY_ID


def test_options_local_pid_remove_requires_breezer() -> None:
    """Test Remove Local PID requires a selected breezer."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_pid"
    assert result["errors"] == {CONF_BREEZER_GUID: "required"}


def test_options_local_pid_remove_selected_breezer_only() -> None:
    """Test Remove Local PID deletes only the selected breezer draft options."""
    flow = _flow(_pid_options())

    result = asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_BREEZER_GUID: BREEZER_GUID,
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
            }
        )
    )

    pid_breezers = flow._options[CONF_PID_BREEZERS]  # noqa: SLF001
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_pid"
    assert BREEZER_GUID not in pid_breezers
    assert SECOND_BREEZER_GUID in pid_breezers


def test_options_local_pid_remove_last_breezer_clears_pid_breezers() -> None:
    """Test removing the last Local PID entry clears the pid_breezers key."""
    flow = _flow(
        {
            CONF_PID_BREEZERS: {
                BREEZER_GUID: _pid_options()[CONF_PID_BREEZERS][BREEZER_GUID],
            }
        }
    )

    result = asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_BREEZER_GUID: BREEZER_GUID,
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_pid"
    assert CONF_PID_BREEZERS not in flow._options  # noqa: SLF001


def test_options_init_configure_presets_opens_presets_step() -> None:
    """Test choosing Configure presets opens the presets step."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 60,
                CONF_OPTIONS_ACTION: OPTIONS_ACTION_CONFIGURE_PRESETS,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"


def test_options_presets_done_returns_to_init() -> None:
    """Test Done in the presets step returns to init without a breezer."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_presets(
            {CONF_BREEZER_GUID: None, CONF_PRESETS_ACTION: PRESETS_ACTION_DONE}
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert flow._breezer_guid is None  # noqa: SLF001


def test_options_presets_add_requires_breezer() -> None:
    """Test adding a preset without a breezer raises a required error."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_presets(
            {CONF_BREEZER_GUID: None, CONF_PRESETS_ACTION: PRESETS_ACTION_ADD}
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"
    assert result["errors"][CONF_BREEZER_GUID] == "required"


def test_options_presets_add_opens_name_form() -> None:
    """Test add with a breezer opens the preset name selection form."""
    flow = _flow()

    result = asyncio.run(
        flow.async_step_presets(
            {CONF_BREEZER_GUID: BREEZER_GUID, CONF_PRESETS_ACTION: PRESETS_ACTION_ADD}
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_add"
    assert flow._breezer_guid == BREEZER_GUID  # noqa: SLF001


def test_options_preset_config_rejects_min_above_max() -> None:
    """Test preset config validates min_speed <= max_speed."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001

    result = asyncio.run(
        flow.async_step_preset_config(
            {CONF_PRESET_MIN_SPEED: 5, CONF_PRESET_MAX_SPEED: 2}
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert result["errors"]["base"] == "min_above_max"


def test_options_preset_config_saves_and_returns_to_presets() -> None:
    """Test a valid preset config is stored under the breezer guid."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001

    result = asyncio.run(
        flow.async_step_preset_config(
            {CONF_PRESET_MIN_SPEED: 4, CONF_PRESET_MAX_SPEED: 6}
        )
    )

    stored = flow._options[CONF_PRESETS][BREEZER_GUID]["boost"]  # noqa: SLF001
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"
    assert stored == {CONF_PRESET_MIN_SPEED: 4, CONF_PRESET_MAX_SPEED: 6}


def test_options_preset_remove_deletes_and_cleans_up() -> None:
    """Test removing the only preset clears the breezer and CONF_PRESETS keys."""
    flow = _flow(_preset_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = asyncio.run(flow.async_step_preset_remove({CONF_PRESET_NAME: "boost"}))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"
    assert CONF_PRESETS not in flow._options  # noqa: SLF001


def test_options_presets_edit_opens_edit_form() -> None:
    """Test choosing Edit with a breezer opens the preset selection form."""
    flow = _flow(_preset_options())

    result = asyncio.run(
        flow.async_step_presets(
            {CONF_BREEZER_GUID: BREEZER_GUID, CONF_PRESETS_ACTION: PRESETS_ACTION_EDIT}
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_edit"
    assert flow._breezer_guid == BREEZER_GUID  # noqa: SLF001


def test_options_preset_edit_selects_and_opens_prefilled_config() -> None:
    """Test selecting a preset to edit opens preset_config (exercising pre-fill)."""
    flow = _flow(_preset_options())
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = asyncio.run(flow.async_step_preset_edit({CONF_PRESET_NAME: "boost"}))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert flow._preset_name == "boost"  # noqa: SLF001


def test_options_preset_add_all_configured_returns_to_presets() -> None:
    """Test Add when every preset is already configured returns to presets."""
    all_presets = {
        name: {CONF_PRESET_MIN_SPEED: 1, CONF_PRESET_MAX_SPEED: 2}
        for name in SUPPORTED_PRESETS
    }
    flow = _flow({CONF_PRESETS: {BREEZER_GUID: all_presets}})
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = asyncio.run(flow.async_step_preset_add())

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"


def test_options_final_done_saves_draft_changes() -> None:
    """Test final init Done saves accumulated draft changes."""
    flow = _flow(_pid_options())
    asyncio.run(
        flow.async_step_local_pid(
            {
                CONF_BREEZER_GUID: BREEZER_GUID,
                CONF_LOCAL_PID_ACTION: LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
            }
        )
    )
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    asyncio.run(flow.async_step_breezer(_pid_form_input(sensor_entity_id="sensor.new")))

    result = asyncio.run(
        flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 45,
                CONF_OPTIONS_ACTION: OPTIONS_ACTION_DONE,
            }
        )
    )

    pid_options = result["data"][CONF_PID_BREEZERS][BREEZER_GUID]
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SCAN_INTERVAL] == 45
    assert pid_options[CONF_CO2_SENSOR_ENTITY_ID] == "sensor.new"
    assert SECOND_BREEZER_GUID in result["data"][CONF_PID_BREEZERS]
