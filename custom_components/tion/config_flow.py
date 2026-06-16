"""Adds config flow (UI flow) for Tion component."""

from collections.abc import Mapping
import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import TionApiError, TionAuthError, TionClient, TionConnectionError
from .const import (
    AUTH_DATA,
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
    DEFAULT_PID_BASE_OUTPUT,
    DEFAULT_PID_KD,
    DEFAULT_PID_KI,
    DEFAULT_PID_KP,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SUPPORTED_PRESETS,
    TionDeviceType,
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


class TionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Tion config flow."""

    VERSION = 1

    @staticmethod
    def _unique_id(username: str) -> str:
        """Return config entry unique id."""
        sha256_hash = hashlib.new("sha256")
        sha256_hash.update(username.encode())
        return sha256_hash.hexdigest()

    async def _async_get_auth_data(
        self, user: str, password: str, interval: int, auth_data: str | None = None
    ) -> tuple[str | None, str | None]:
        """Get auth data and map client errors to config flow errors."""
        session = async_create_clientsession(self.hass)
        api = TionClient(
            session, user, password, min_update_interval_sec=interval, auth=auth_data
        )
        try:
            return await api.async_validate_auth(), None
        except TionAuthError:
            return None, "invalid_auth"
        except TionConnectionError:
            return None, "cannot_connect"
        except TionApiError as err:
            _LOGGER.warning("Unexpected Tion API response during auth: %s", err)
            return None, "unknown"
        except Exception:
            _LOGGER.exception("Unexpected exception during Tion auth")
            return None, "unknown"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return TionOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step user."""

        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})

            auth_data, error = await self._async_get_auth_data(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                DEFAULT_SCAN_INTERVAL,
            )

            if error is not None:
                errors["base"] = error
            else:
                await self.async_set_unique_id(
                    self._unique_id(user_input[CONF_USERNAME])
                )
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        AUTH_DATA: auth_data,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=""): str,
                    vol.Required(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        errors: dict[str, str] = {}
        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            username = reauth_entry.data[CONF_USERNAME]
            auth_data, error = await self._async_get_auth_data(
                username,
                user_input[CONF_PASSWORD],
                reauth_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )

            if error is not None:
                errors["base"] = error
            elif auth_data is not None:
                await self.async_set_unique_id(self._unique_id(username))
                self._abort_if_unique_id_mismatch(reason="wrong_account")

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    title=username,
                    data={
                        **reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        AUTH_DATA: auth_data,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )


class TionOptionsFlow(OptionsFlow):
    """Tion options flow handler."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Tion options flow."""
        self._entry_id = config_entry.entry_id
        self._options = dict(config_entry.options)
        self._breezer_guid: str | None = None
        self._preset_name: str | None = None

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
        """Select a preset name to add for the breezer."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._preset_name = user_input[CONF_PRESET_NAME]
            return await self.async_step_preset_config()

        configured = self._breezer_presets(self._breezer_guid)
        available = [name for name in SUPPORTED_PRESETS if name not in configured]
        if not available:
            errors["base"] = "all_presets_configured"
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
                }
            ),
        )

    async def async_step_preset_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure min/max speed for the selected preset."""
        errors: dict[str, str] = {}

        if user_input is not None:
            min_speed = int(user_input[CONF_PRESET_MIN_SPEED])
            max_speed = int(user_input[CONF_PRESET_MAX_SPEED])
            if min_speed > max_speed:
                errors["base"] = "min_above_max"
            else:
                presets = dict(self._options.get(CONF_PRESETS, {}))
                breezer_presets = dict(presets.get(self._breezer_guid, {}))
                breezer_presets[self._preset_name] = {
                    CONF_PRESET_MIN_SPEED: min_speed,
                    CONF_PRESET_MAX_SPEED: max_speed,
                }
                presets[self._breezer_guid] = breezer_presets
                self._options[CONF_PRESETS] = presets
                self._preset_name = None
                return await self.async_step_presets()

        return self.async_show_form(
            step_id="preset_config",
            data_schema=self._preset_schema(),
            description_placeholders={"preset_name": self._preset_name or ""},
            errors=errors,
        )

    async def async_step_preset_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an existing preset to edit."""
        configured = self._breezer_presets(self._breezer_guid)
        if not configured:
            return await self.async_step_presets()

        if user_input is not None:
            self._preset_name = user_input[CONF_PRESET_NAME]
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
        """Return the min/max speed schema for the current preset."""
        preset = self._breezer_presets(self._breezer_guid).get(self._preset_name, {})
        max_speed = self._breezer_max_speed(self._breezer_guid)
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
