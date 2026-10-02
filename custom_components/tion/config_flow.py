"""Adds config flow (UI flow) for Tion component."""

from collections.abc import Mapping
import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api.auth import (
    LOGIN_ERROR_CODE_EXPIRED,
    LOGIN_ERROR_PASSWORD_NOT_SET,
    TionAuth,
    TionLoginError,
    TionTokens,
    async_create_auth,
)
from .api.device_key import TionDeviceKey
from .api.exceptions import TionConnectionError
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
    CONF_PID_KD,
    CONF_PID_KI,
    CONF_PID_KP,
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    CONF_PRESETS,
    DEFAULT_PID_BASE_OUTPUT,
    DEFAULT_PID_KD,
    DEFAULT_PID_KI,
    DEFAULT_PID_KP,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SUPPORTED_PRESETS,
    TionDeviceType,
    TionPresetType,
)
from .coordinator import TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

CONF_OPTIONS_ACTION = "options_action"
CONF_LOCAL_PID_ACTION = "local_pid_action"

OPTIONS_ACTION_DONE = "done"
OPTIONS_ACTION_CONFIGURE_LOCAL_PID = "configure_local_pid"

LOCAL_PID_ACTION_DONE = "done"
LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID = "configure_breezer_pid"
LOCAL_PID_ACTION_REMOVE_BREEZER_PID = "remove_breezer_pid"

OPTIONS_ACTION_CONFIGURE_PRESETS = "configure_presets"

CONF_PRESETS_ACTION = "presets_action"
CONF_PRESET_NAME = "preset_name"

PRESETS_ACTION_ADD = "add"
PRESETS_ACTION_DONE = "done"
PRESETS_ACTION_EDIT = "edit"
PRESETS_ACTION_REMOVE = "remove"

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
    def _unique_id(email: str) -> str:
        """Return config entry unique id."""
        return hashlib.sha256(email.encode()).hexdigest()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return TionOptionsFlow(config_entry)

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
            **tokens.as_entry_data(),
        }
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), title=self._email, data=data
            )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._email, data=data)


class TionOptionsFlow(OptionsFlow):
    """Tion options flow handler."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Tion options flow."""
        self._entry_id = config_entry.entry_id
        self._options = dict(config_entry.options)
        self._breezer_guid: str | None = None
        self._preset_name: str | None = None
        self._preset_type: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""

        if user_input is not None:
            self._options[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]
            if user_input[CONF_OPTIONS_ACTION] == OPTIONS_ACTION_CONFIGURE_LOCAL_PID:
                return await self.async_step_local_pid()
            if user_input[CONF_OPTIONS_ACTION] == OPTIONS_ACTION_CONFIGURE_PRESETS:
                return await self.async_step_presets()

            return self.async_create_entry(title="", data=self._options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self._options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10)),
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
        )

    async def async_step_local_pid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage local PID action selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            local_pid_action = user_input[CONF_LOCAL_PID_ACTION]
            self._breezer_guid = user_input.get(CONF_BREEZER_GUID)

            if local_pid_action == LOCAL_PID_ACTION_DONE:
                self._breezer_guid = None
                return await self.async_step_init()

            if self._breezer_guid is None:
                errors[CONF_BREEZER_GUID] = "required"
            elif local_pid_action == LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID:
                return await self.async_step_breezer()
            elif local_pid_action == LOCAL_PID_ACTION_REMOVE_BREEZER_PID:
                pid_breezers = dict(self._options.get(CONF_PID_BREEZERS, {}))
                pid_breezers.pop(self._breezer_guid, None)

                if pid_breezers:
                    self._options[CONF_PID_BREEZERS] = pid_breezers
                else:
                    self._options.pop(CONF_PID_BREEZERS, None)

        return self.async_show_form(
            step_id="local_pid",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_BREEZER_GUID, default=None): vol.Any(
                        None,
                        selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=self._breezer_options(),
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        ),
                    ),
                    vol.Required(
                        CONF_LOCAL_PID_ACTION, default=LOCAL_PID_ACTION_DONE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                LOCAL_PID_ACTION_CONFIGURE_BREEZER_PID,
                                LOCAL_PID_ACTION_REMOVE_BREEZER_PID,
                                LOCAL_PID_ACTION_DONE,
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="local_pid_menu_selector",
                        )
                    ),
                }
            ),
            errors=errors,
        )

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
                    CONF_PID_ENABLED: user_input[CONF_PID_ENABLED],
                    CONF_CO2_SENSOR_ENTITY_ID: co2_sensor_entity_id,
                    CONF_PID_BASE_OUTPUT: float(user_input[CONF_PID_BASE_OUTPUT]),
                    CONF_PID_KP: float(user_input[CONF_PID_KP]),
                    CONF_PID_KI: float(user_input[CONF_PID_KI]),
                    CONF_PID_KD: float(user_input[CONF_PID_KD]),
                }
                self._options[CONF_PID_BREEZERS] = pid_breezers

                return await self.async_step_local_pid()

        return self.async_show_form(
            step_id="breezer",
            data_schema=self._pid_schema(),
            errors=errors,
        )

    async def async_step_presets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage preset action selection for a breezer."""
        errors: dict[str, str] = {}

        if user_input is not None:
            presets_action = user_input[CONF_PRESETS_ACTION]
            self._breezer_guid = user_input.get(CONF_BREEZER_GUID)

            if presets_action == PRESETS_ACTION_DONE:
                self._breezer_guid = None
                return await self.async_step_init()

            if self._breezer_guid is None:
                errors[CONF_BREEZER_GUID] = "required"
            elif presets_action == PRESETS_ACTION_ADD:
                return await self.async_step_preset_add()
            elif presets_action == PRESETS_ACTION_EDIT:
                return await self.async_step_preset_edit()
            elif presets_action == PRESETS_ACTION_REMOVE:
                return await self.async_step_preset_remove()

        return self.async_show_form(
            step_id="presets",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_BREEZER_GUID, default=None): vol.Any(
                        None,
                        selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=self._breezer_options(),
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        ),
                    ),
                    vol.Required(
                        CONF_PRESETS_ACTION, default=PRESETS_ACTION_DONE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                PRESETS_ACTION_ADD,
                                PRESETS_ACTION_EDIT,
                                PRESETS_ACTION_REMOVE,
                                PRESETS_ACTION_DONE,
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="presets_menu_selector",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_preset_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a preset name and type to add for the breezer."""
        if user_input is not None:
            self._preset_name = user_input[CONF_PRESET_NAME]
            self._preset_type = user_input[CONF_PRESET_TYPE]
            return await self.async_step_preset_config()

        configured = self._breezer_presets(self._breezer_guid)
        available = [name for name in SUPPORTED_PRESETS if name not in configured]
        if not available:
            return await self.async_step_presets()

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
                        CONF_PRESET_TYPE, default=TionPresetType.AUTO.value
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                TionPresetType.AUTO.value,
                                TionPresetType.MANUAL.value,
                            ],
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
                return await self.async_step_presets()

            min_speed = int(user_input[CONF_PRESET_MIN_SPEED])
            max_speed = int(user_input[CONF_PRESET_MAX_SPEED])
            if min_speed > max_speed:
                errors["base"] = "min_above_max"
            else:
                self._store_preset(
                    {
                        CONF_PRESET_TYPE: TionPresetType.AUTO.value,
                        CONF_PRESET_MIN_SPEED: min_speed,
                        CONF_PRESET_MAX_SPEED: max_speed,
                    }
                )
                return await self.async_step_presets()

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
            return await self.async_step_presets()

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
            return await self.async_step_presets()

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

            return await self.async_step_presets()

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

    def _breezer_presets(self, breezer_guid: str | None) -> dict[str, Any]:
        """Return stored presets for a breezer."""
        if breezer_guid is None:
            return {}
        return self._options.get(CONF_PRESETS, {}).get(breezer_guid, {})

    def _breezer_max_speed(self, breezer_guid: str | None) -> int:
        """Return the configured max speed for a breezer (defaults to 6)."""
        coordinator: TionDataUpdateCoordinator | None = self.hass.data.get(
            DOMAIN, {}
        ).get(self._entry_id)
        if coordinator is not None and coordinator.data is not None:
            for device in coordinator.get_devices():
                if device.guid == breezer_guid:
                    return getattr(device, "max_speed", 6)
        return 6

    def _preset_schema(self) -> vol.Schema:
        """Return the config schema for the current preset type."""
        preset = self._breezer_presets(self._breezer_guid).get(self._preset_name, {})
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
                    default=preset.get(CONF_PRESET_MIN_SPEED, 0),
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

    def _breezer_options(self) -> list[dict[str, str]]:
        """Return selectable breezers for the config entry."""
        coordinator: TionDataUpdateCoordinator | None = self.hass.data.get(
            DOMAIN, {}
        ).get(self._entry_id)
        if coordinator is None or coordinator.data is None:
            return []

        return [
            {"label": device.name or device.guid, "value": device.guid}
            for device in coordinator.get_devices()
            if device.guid
            and device.type
            in (
                TionDeviceType.BREEZER_O2,
                TionDeviceType.BREEZER_3S,
                TionDeviceType.BREEZER_4S,
            )
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
