"""Adds config flow (UI flow) for Tion component."""

from collections.abc import Mapping
from functools import cached_property
import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_USERNAME,
    Platform,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api.auth import (
    LOGIN_ERROR_CODE_EXPIRED,
    LOGIN_ERROR_PASSWORD_NOT_SET,
    TionAuth,
    TionLoginError,
    TionTokens,
)
from .api.device_key import TionDeviceKey
from .api.exceptions import TionConnectionError
from .api.views import Breezer, view
from .captcha_view import CAPTCHA_STEP_ID, async_register_captcha_view, captcha_page_url
from .const import (
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
    DEFAULT_PID_BASE_OUTPUT,
    DEFAULT_PID_INTERVAL,
    DEFAULT_PID_KD,
    DEFAULT_PID_KI,
    DEFAULT_PID_KP,
    DEFAULT_PID_MIN_SPEED,
    DOMAIN,
    PID_INTERVAL_RANGE,
    SUPPORTED_PRESETS,
    TionPresetType,
)
from .coordinator import TionConfigEntry
from .pid_manager import is_pid_set_up
from .session import async_create_auth

_LOGGER = logging.getLogger(__name__)

CONF_OPTIONS_ACTION = "options_action"

OPTIONS_ACTION_DONE = "done"
OPTIONS_ACTION_CONFIGURE_LOCAL_PID = "configure_local_pid"

OPTIONS_ACTION_CONFIGURE_PRESETS = "configure_presets"

CONF_PRESET_NAME = "preset_name"

# PID settings the number entities edit while the options flow may be open.
PID_ENTITY_SETTINGS = (CONF_PID_MIN_SPEED, CONF_PID_MAX_SPEED, CONF_PID_TARGET_CO2)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})
STEP_CODE_SCHEMA = vol.Schema({vol.Required(CONF_CODE): str})


class TionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Tion config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._email = ""
        self._password = ""
        self._auth: TionAuth | None = None
        self._login_error: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: TionConfigEntry) -> TionOptionsFlow:
        """Return the options flow: local PID and presets."""
        return TionOptionsFlow()

    @staticmethod
    def _unique_id(email: str) -> str:
        """Return config entry unique id."""
        return hashlib.sha256(email.encode()).hexdigest()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Tion account e-mail and password."""
        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            await self.async_set_unique_id(
                self._unique_id(self._email), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            return await self._async_start_login(user_input[CONF_PASSWORD])

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {CONF_EMAIL: self._email}
            ),
            errors=self._pop_login_error(),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a new login for the configured account."""
        # Entries created before the v4 login keep the e-mail as the username.
        self._email = entry_data.get(CONF_EMAIL) or entry_data[CONF_USERNAME]
        await self.async_set_unique_id(self._unique_id(self._email))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password of the configured account."""
        if user_input is not None:
            return await self._async_start_login(user_input[CONF_PASSWORD])

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"email": self._email},
            errors=self._pop_login_error(),
        )

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the user to the captcha page; resumed by the captcha view."""
        if user_input is None:
            async_register_captcha_view(self.hass)
            return self.async_external_step(
                step_id=CAPTCHA_STEP_ID, url=captcha_page_url(self.flow_id)
            )

        # An external step may only end with external_step_done, so a failure
        # is carried back to the credentials form instead of shown here.
        password, self._password = self._password, ""
        try:
            await self._require_auth().async_get_confirmation_code(
                self._email, password, user_input[CONF_CAPTCHA_TOKEN]
            )
        except TionLoginError as err:
            self._login_error = err.reason
        except TionConnectionError:
            self._login_error = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error requesting the Tion e-mail code")
            self._login_error = "unknown"
        else:
            return self.async_external_step_done(next_step_id="code")
        return self.async_external_step_done(next_step_id=self._credentials_step_id)

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the code from the e-mail and finish the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            auth = self._require_auth()
            try:
                await auth.async_check_confirmation_code(user_input[CONF_CODE])
                tokens = await auth.async_get_token()
            except TionLoginError as err:
                if err.reason == LOGIN_ERROR_PASSWORD_NOT_SET:
                    return self.async_abort(reason=LOGIN_ERROR_PASSWORD_NOT_SET)
                if err.reason == LOGIN_ERROR_CODE_EXPIRED:
                    self._login_error = err.reason
                    if self.source == SOURCE_REAUTH:
                        return await self.async_step_reauth_confirm()
                    return await self.async_step_user()
                errors["base"] = err.reason
            except TionConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error finishing the Tion login")
                errors["base"] = "unknown"
            else:
                return self._finish_login(auth.device_key, tokens)

        return self.async_show_form(
            step_id="code",
            data_schema=STEP_CODE_SCHEMA,
            description_placeholders={"email": self._email},
            errors=errors,
        )

    @property
    def _credentials_step_id(self) -> str:
        return "reauth_confirm" if self.source == SOURCE_REAUTH else "user"

    async def _async_start_login(self, password: str) -> ConfigFlowResult:
        self._password = password
        self._auth = await async_create_auth(self.hass, TionDeviceKey.generate())
        return await self.async_step_captcha()

    def _require_auth(self) -> TionAuth:
        if self._auth is None:
            raise RuntimeError("The credentials step creates the session first")
        return self._auth

    def _pop_login_error(self) -> dict[str, str]:
        error, self._login_error = self._login_error, None
        return {"base": error} if error else {}

    def _finish_login(
        self, device_key: TionDeviceKey, tokens: TionTokens
    ) -> ConfigFlowResult:
        data = {
            CONF_EMAIL: self._email,
            CONF_DEVICE_KEY: device_key.to_pem(),
            CONF_DEVICE_KEY_ID: device_key.key_id(),
            **tokens.as_dict(),
        }
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), title=self._email, data=data
            )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._email, data=data)


class TionOptionsFlow(OptionsFlowWithReload):
    """Tion options flow: local PID and speed presets per breezer."""

    config_entry: TionConfigEntry

    def __init__(self) -> None:
        """Initialize Tion options flow."""
        self._breezer_guid: str | None = None
        self._preset_name: str | None = None
        self._preset_type: str | None = None
        self._pid_removed: set[str] = set()

    @cached_property
    def _options(self) -> dict[str, Any]:
        """Return the draft of the options, saved when the flow finishes."""
        return dict(self.config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""

        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input[CONF_OPTIONS_ACTION]
            if action == OPTIONS_ACTION_DONE:
                return self.async_create_entry(title="", data=self._options_to_save())

            breezers = self._breezers()
            if not breezers:
                errors["base"] = "no_breezers"
            elif action == OPTIONS_ACTION_CONFIGURE_LOCAL_PID:
                if len(breezers) == 1:
                    self._breezer_guid = breezers[0].id
                    return await self.async_step_local_pid_menu()
                return await self.async_step_local_pid()
            elif len(breezers) == 1:
                self._breezer_guid = breezers[0].id
                return await self.async_step_presets_menu()
            else:
                return await self.async_step_presets()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OPTIONS_ACTION, default=OPTIONS_ACTION_DONE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                OPTIONS_ACTION_CONFIGURE_LOCAL_PID,
                                OPTIONS_ACTION_CONFIGURE_PRESETS,
                                OPTIONS_ACTION_DONE,
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="init_menu_selector",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_local_pid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the breezer to set up local PID for."""
        if user_input is not None:
            self._breezer_guid = user_input[CONF_BREEZER_GUID]
            return await self.async_step_local_pid_menu()

        return self.async_show_form(
            step_id="local_pid", data_schema=self._breezer_schema()
        )

    async def async_step_local_pid_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the local PID actions the breezer can take."""
        assert self._breezer_guid is not None
        menu_options = ["breezer"]
        sensor = "—"
        if self._pid_configured(self._breezer_guid):
            menu_options.append("pid_remove")
            sensor = self._pid_options(self._breezer_guid)[CONF_CO2_SENSOR_ENTITY_ID]
        menu_options.append("init")

        return self.async_show_menu(
            step_id="local_pid_menu",
            menu_options=menu_options,
            description_placeholders={
                "breezer": self._breezer_name(self._breezer_guid),
                "sensor": sensor,
            },
        )

    async def async_step_pid_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove local PID from the breezer."""
        assert self._breezer_guid is not None
        pid_breezers = dict(self._options.get(CONF_PID_BREEZERS, {}))
        pid_breezers.pop(self._breezer_guid, None)
        self._pid_removed.add(self._breezer_guid)

        if pid_breezers:
            self._options[CONF_PID_BREEZERS] = pid_breezers
        else:
            self._options.pop(CONF_PID_BREEZERS, None)

        return await self.async_step_local_pid_menu()

    async def async_step_breezer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage local PID options for a breezer."""
        errors: dict[str, str] = {}

        if user_input is not None and self._breezer_guid is not None:
            co2_sensor_entity_id = user_input.get(CONF_CO2_SENSOR_ENTITY_ID)
            if user_input[CONF_PID_ENABLED] and not co2_sensor_entity_id:
                errors[CONF_CO2_SENSOR_ENTITY_ID] = "required"
            else:
                pid_breezers = dict(self._options.get(CONF_PID_BREEZERS, {}))
                pid_breezers[self._breezer_guid] = {
                    **pid_breezers.get(self._breezer_guid, {}),
                    CONF_PID_ENABLED: user_input[CONF_PID_ENABLED],
                    CONF_CO2_SENSOR_ENTITY_ID: co2_sensor_entity_id,
                    CONF_PID_INTERVAL: int(user_input[CONF_PID_INTERVAL]),
                    CONF_PID_BASE_OUTPUT: float(user_input[CONF_PID_BASE_OUTPUT]),
                    CONF_PID_KP: float(user_input[CONF_PID_KP]),
                    CONF_PID_KI: float(user_input[CONF_PID_KI]),
                    CONF_PID_KD: float(user_input[CONF_PID_KD]),
                }
                self._options[CONF_PID_BREEZERS] = pid_breezers

                return await self.async_step_local_pid_menu()

        return self.async_show_form(
            step_id="breezer",
            data_schema=self._pid_schema(),
            errors=errors,
        )

    async def async_step_presets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the breezer whose presets to change."""
        if user_input is not None:
            self._breezer_guid = user_input[CONF_BREEZER_GUID]
            return await self.async_step_presets_menu()

        return self.async_show_form(
            step_id="presets", data_schema=self._breezer_schema()
        )

    async def async_step_presets_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the preset actions the breezer can take."""
        assert self._breezer_guid is not None
        configured = self._breezer_presets(self._breezer_guid)
        menu_options = []
        if self._available_preset_names():
            menu_options.append("preset_add")
        if configured:
            menu_options += ["preset_edit", "preset_remove"]
        menu_options.append("init")

        return self.async_show_menu(
            step_id="presets_menu",
            menu_options=menu_options,
            description_placeholders={
                "breezer": self._breezer_name(self._breezer_guid),
                "count": str(len(configured)),
            },
        )

    async def async_step_preset_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a preset name and type to add for the breezer."""
        if user_input is not None:
            self._preset_name = user_input[CONF_PRESET_NAME]
            self._preset_type = user_input[CONF_PRESET_TYPE]
            return await self.async_step_preset_config()

        available = self._available_preset_names()
        if not available:
            return await self.async_step_presets_menu()

        return self.async_show_form(
            step_id="preset_add",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRESET_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=available,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="preset_name_selector",
                        )
                    ),
                    vol.Required(
                        CONF_PRESET_TYPE, default=TionPresetType.MANUAL.value
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._preset_types(),
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="preset_type_selector",
                        )
                    ),
                }
            ),
        )

    async def async_step_preset_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the speed(s) for the selected preset and type."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if self._preset_type == TionPresetType.MANUAL:
                self._store_preset(
                    {
                        CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
                        CONF_PRESET_SPEED: int(user_input[CONF_PRESET_SPEED]),
                    }
                )
                return await self.async_step_presets_menu()

            min_speed = int(user_input[CONF_PRESET_MIN_SPEED])
            max_speed = int(user_input[CONF_PRESET_MAX_SPEED])
            if min_speed > max_speed:
                errors["base"] = "min_above_max"
            else:
                self._store_preset(
                    {
                        CONF_PRESET_TYPE: TionPresetType.LOCAL_PID.value,
                        CONF_PRESET_MIN_SPEED: min_speed,
                        CONF_PRESET_MAX_SPEED: max_speed,
                    }
                )
                return await self.async_step_presets_menu()

        return self.async_show_form(
            step_id="preset_config",
            data_schema=self._preset_schema(),
            description_placeholders={"preset_name": self._preset_name or ""},
            errors=errors,
        )

    def _store_preset(self, preset: dict[str, Any]) -> None:
        """Persist a configured preset for the current breezer."""
        presets = dict(self._options.get(CONF_PRESETS, {}))
        breezer_presets = dict(presets.get(self._breezer_guid, {}))
        breezer_presets[self._preset_name] = preset
        presets[self._breezer_guid] = breezer_presets
        self._options[CONF_PRESETS] = presets
        self._preset_name = None
        self._preset_type = None

    async def async_step_preset_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an existing preset to edit."""
        configured = self._breezer_presets(self._breezer_guid)
        if not configured:
            return await self.async_step_presets_menu()

        if user_input is not None:
            self._preset_name = user_input[CONF_PRESET_NAME]
            self._preset_type = configured[self._preset_name][CONF_PRESET_TYPE]
            return await self.async_step_preset_config()

        return self.async_show_form(
            step_id="preset_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRESET_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(configured),
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="preset_name_selector",
                        )
                    ),
                }
            ),
        )

    async def async_step_preset_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a preset from the breezer."""
        configured = self._breezer_presets(self._breezer_guid)
        if not configured:
            return await self.async_step_presets_menu()

        if user_input is not None:
            presets = dict(self._options.get(CONF_PRESETS, {}))
            breezer_presets = dict(presets.get(self._breezer_guid, {}))
            breezer_presets.pop(user_input[CONF_PRESET_NAME], None)

            if breezer_presets:
                presets[self._breezer_guid] = breezer_presets
            else:
                presets.pop(self._breezer_guid, None)

            if presets:
                self._options[CONF_PRESETS] = presets
            else:
                self._options.pop(CONF_PRESETS, None)

            return await self.async_step_presets_menu()

        return self.async_show_form(
            step_id="preset_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRESET_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(configured),
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="preset_name_selector",
                        )
                    ),
                }
            ),
        )

    def _available_preset_names(self) -> list[str]:
        """Return the supported preset names the breezer has not taken."""
        configured = self._breezer_presets(self._breezer_guid)
        return [name for name in SUPPORTED_PRESETS if name not in configured]

    def _breezer_presets(self, breezer_guid: str | None) -> dict[str, Any]:
        """Return a breezer's stored presets of the types this version offers."""
        if breezer_guid is None:
            return {}
        presets = self._options.get(CONF_PRESETS, {}).get(breezer_guid, {})
        return {
            name: preset
            for name, preset in presets.items()
            if preset.get(CONF_PRESET_TYPE) in tuple(TionPresetType)
        }

    def _preset_types(self) -> list[str]:
        """Return the preset types for the breezer: PID ones need PID set up."""
        types = [TionPresetType.MANUAL.value]
        if self._pid_configured(self._breezer_guid):
            types.append(TionPresetType.LOCAL_PID.value)
        return types

    def _pid_configured(self, breezer_guid: str | None) -> bool:
        """Return whether the draft sets up local PID for the breezer."""
        return breezer_guid is not None and is_pid_set_up(
            self._pid_options(breezer_guid)
        )

    def _options_to_save(self) -> dict[str, Any]:
        """Return the draft with the PID settings entities changed meanwhile."""
        draft = self._options.get(CONF_PID_BREEZERS)
        if not draft:
            return self._options
        current = self.config_entry.options.get(CONF_PID_BREEZERS, {})
        pid_breezers = {}
        for breezer_id, pid_options in draft.items():
            live = (
                {} if breezer_id in self._pid_removed else current.get(breezer_id, {})
            )
            pid_breezers[breezer_id] = {
                **pid_options,
                **{key: live[key] for key in PID_ENTITY_SETTINGS if key in live},
            }
        return {**self._options, CONF_PID_BREEZERS: pid_breezers}

    def _breezers(self) -> list[Breezer]:
        """Return the breezers of the loaded config entry."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return []
        account = self.config_entry.runtime_data.data
        return [
            device_view
            for device in account.devices()
            if isinstance(device_view := view(device), Breezer)
        ]

    def _breezer_max_speed(self, breezer_guid: str | None) -> int:
        """Return the max speed of a breezer (defaults to 6)."""
        for breezer in self._breezers():
            if breezer.id == breezer_guid and breezer.speed_max is not None:
                return breezer.speed_max
        return 6

    def _preset_schema(self) -> vol.Schema:
        """Return the config schema for the current preset type."""
        presets = self._breezer_presets(self._breezer_guid)
        preset = presets.get(self._preset_name, {}) if self._preset_name else {}
        max_speed = self._breezer_max_speed(self._breezer_guid)

        if self._preset_type == TionPresetType.MANUAL:
            return vol.Schema(
                {
                    vol.Required(
                        CONF_PRESET_SPEED,
                        default=preset.get(CONF_PRESET_SPEED, 1),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=max_speed,
                            step=1,
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            )

        return vol.Schema(
            {
                vol.Required(
                    CONF_PRESET_MIN_SPEED,
                    default=preset.get(CONF_PRESET_MIN_SPEED, DEFAULT_PID_MIN_SPEED),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=max_speed,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(
                    CONF_PRESET_MAX_SPEED,
                    default=preset.get(CONF_PRESET_MAX_SPEED, max_speed),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=max_speed,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

    def _breezer_schema(self) -> vol.Schema:
        """Return the schema that selects one of the account's breezers."""
        return vol.Schema(
            {
                vol.Required(CONF_BREEZER_GUID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._breezer_options(),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    def _breezer_name(self, breezer_guid: str) -> str:
        """Return the name a breezer is shown with."""
        for breezer in self._breezers():
            if breezer.id == breezer_guid:
                return breezer.device.name or breezer.id
        return breezer_guid

    def _breezer_options(self) -> list[selector.SelectOptionDict]:
        """Return selectable breezers for the config entry."""
        return [
            selector.SelectOptionDict(
                value=breezer.id, label=breezer.device.name or breezer.id
            )
            for breezer in self._breezers()
        ]

    def _pid_options(self, breezer_guid: str) -> dict[str, Any]:
        """Return stored PID options for a breezer."""
        return self._options.get(CONF_PID_BREEZERS, {}).get(breezer_guid, {})

    def _pid_schema(self) -> vol.Schema:
        """Return breezer PID options schema."""
        pid_options = (
            self._pid_options(self._breezer_guid) if self._breezer_guid else {}
        )

        co2_sensor_entity_id = pid_options.get(CONF_CO2_SENSOR_ENTITY_ID)
        co2_sensor_key = (
            vol.Optional(CONF_CO2_SENSOR_ENTITY_ID, default=co2_sensor_entity_id)
            if co2_sensor_entity_id
            else vol.Optional(CONF_CO2_SENSOR_ENTITY_ID)
        )

        return vol.Schema(
            {
                vol.Required(
                    CONF_PID_ENABLED,
                    default=pid_options.get(
                        CONF_PID_ENABLED, bool(co2_sensor_entity_id)
                    ),
                ): bool,
                co2_sensor_key: selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=Platform.SENSOR,
                        device_class=SensorDeviceClass.CO2,
                    )
                ),
                vol.Required(
                    CONF_PID_INTERVAL,
                    default=pid_options.get(CONF_PID_INTERVAL, DEFAULT_PID_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=PID_INTERVAL_RANGE[0],
                        max=PID_INTERVAL_RANGE[1],
                        step=1,
                        unit_of_measurement=UnitOfTime.SECONDS,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PID_BASE_OUTPUT,
                    default=pid_options.get(
                        CONF_PID_BASE_OUTPUT, DEFAULT_PID_BASE_OUTPUT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PID_KP, default=pid_options.get(CONF_PID_KP, DEFAULT_PID_KP)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step=0.001,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PID_KI, default=pid_options.get(CONF_PID_KI, DEFAULT_PID_KI)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step=0.001,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PID_KD, default=pid_options.get(CONF_PID_KD, DEFAULT_PID_KD)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step=0.001,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
