"""Tests for Tion config and options flows."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tion import config_flow
from custom_components.tion.auth import (
    LOGIN_ERROR_CODE_EXPIRED,
    LOGIN_ERROR_INVALID_AUTH,
    LOGIN_ERROR_INVALID_CAPTCHA,
    LOGIN_ERROR_INVALID_CODE,
    LOGIN_ERROR_PASSWORD_NOT_SET,
    TionLoginError,
    TionTokens,
)
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
    TionConfigFlow,
    TionOptionsFlow,
)
from custom_components.tion.const import (
    AUTH_DATA,
    CONF_BREEZER_GUID,
    CONF_CAPTCHA_TOKEN,
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_DEVICE_KEY,
    CONF_DEVICE_KEY_ID,
    CONF_PID_BASE_OUTPUT,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    CONF_PRESETS,
    DOMAIN,
    SUPPORTED_PRESETS,
    TionDeviceType,
    TionPresetType,
)
from custom_components.tion.device_key import TionDeviceKey
from custom_components.tion.exceptions import TionApiError, TionConnectionError
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers.typing import UNDEFINED

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
                "boost": {
                    CONF_PRESET_TYPE: TionPresetType.AUTO.value,
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
    """Test add with a breezer opens the preset name and type selection form."""
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
    """Test auto preset config validates min_speed <= max_speed."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001
    flow._preset_type = TionPresetType.AUTO.value  # noqa: SLF001

    result = asyncio.run(
        flow.async_step_preset_config(
            {CONF_PRESET_MIN_SPEED: 5, CONF_PRESET_MAX_SPEED: 2}
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert result["errors"]["base"] == "min_above_max"


def test_options_preset_config_saves_auto_preset() -> None:
    """Test a valid auto preset config is stored with its type."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001
    flow._preset_type = TionPresetType.AUTO.value  # noqa: SLF001

    result = asyncio.run(
        flow.async_step_preset_config(
            {CONF_PRESET_MIN_SPEED: 4, CONF_PRESET_MAX_SPEED: 6}
        )
    )

    stored = flow._options[CONF_PRESETS][BREEZER_GUID]["boost"]  # noqa: SLF001
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"
    assert stored == {
        CONF_PRESET_TYPE: TionPresetType.AUTO.value,
        CONF_PRESET_MIN_SPEED: 4,
        CONF_PRESET_MAX_SPEED: 6,
    }


def test_options_preset_config_saves_manual_preset() -> None:
    """Test a valid manual preset config is stored with its target speed."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001
    flow._preset_name = "boost"  # noqa: SLF001
    flow._preset_type = TionPresetType.MANUAL.value  # noqa: SLF001

    result = asyncio.run(flow.async_step_preset_config({CONF_PRESET_SPEED: 5}))

    stored = flow._options[CONF_PRESETS][BREEZER_GUID]["boost"]  # noqa: SLF001
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "presets"
    assert stored == {
        CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
        CONF_PRESET_SPEED: 5,
    }


def test_options_preset_add_selects_name_and_type_opens_config() -> None:
    """Test selecting a preset name and type opens the config form."""
    flow = _flow()
    flow._breezer_guid = BREEZER_GUID  # noqa: SLF001

    result = asyncio.run(
        flow.async_step_preset_add(
            {
                CONF_PRESET_NAME: "boost",
                CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_config"
    assert flow._preset_name == "boost"  # noqa: SLF001
    assert flow._preset_type == TionPresetType.MANUAL.value  # noqa: SLF001


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
    assert flow._preset_type == TionPresetType.AUTO.value  # noqa: SLF001


def test_options_preset_add_all_configured_returns_to_presets() -> None:
    """Test Add when every preset is already configured returns to presets."""
    all_presets = {
        name: {
            CONF_PRESET_TYPE: TionPresetType.AUTO.value,
            CONF_PRESET_MIN_SPEED: 1,
            CONF_PRESET_MAX_SPEED: 2,
        }
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
        self.flow = SimpleNamespace(async_progress_by_handler=lambda *a, **kw: [])

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
    """Credentials lead to the captcha page for this flow."""
    hass = FakeFlowHass()
    flow = _config_flow(hass)

    result = await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "captcha"
    assert result["url"] == f"/api/tion/captcha/{FLOW_ID}"
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
async def test_captcha_token_requests_code(auths: list[FakeAuth]) -> None:
    """The captcha token is used at once and the flow moves to the code form."""
    flow = _config_flow(FakeFlowHass())
    await flow.async_step_user({CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"})

    result = await flow.async_step_captcha({CONF_CAPTCHA_TOKEN: "dD0xtoken"})

    assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
    assert result["step_id"] == "code"
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
        **TOKENS.as_entry_data(),
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
            {CONF_USERNAME: EMAIL, CONF_PASSWORD: "old", AUTH_DATA: {"api": "t"}},
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
        **TOKENS.as_entry_data(),
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
    assert {"already_configured", "password_not_set", "wrong_account"} <= set(
        config["abort"]
    )
