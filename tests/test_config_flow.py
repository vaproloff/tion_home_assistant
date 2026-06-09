"""Tests for Tion config and options flows."""

import asyncio
from types import SimpleNamespace
from typing import Any

from custom_components.tion.config_flow import (
    CONF_LOCAL_PID_ACTION,
    CONF_OPTIONS_ACTION,
    LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID,
    LOCAL_PID_ACTION_DONE,
    LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
    OPTIONS_ACTION_CONFIGURE_LOCAL_PID,
    OPTIONS_ACTION_DONE,
    TionOptionsFlow,
)
from custom_components.tion.const import (
    CONF_BREEZER_GUID,
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BREEZERS,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_ENABLED,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    DOMAIN,
    TionDeviceType,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType

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
