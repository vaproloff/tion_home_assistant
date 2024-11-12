"""Adds config flow (UI flow) for Tion component."""

from collections.abc import Mapping
import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import TionClient
from .const import AUTH_DATA, DOMAIN

DEFAULT_SCAN_INTERVAL = 60

_LOGGER = logging.getLogger(__name__)


class TionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Tion config flow."""

    VERSION = 1

    async def _get_auth_data(
        self, user, password, interval, auth_data=None
    ) -> str | None:
        session = async_create_clientsession(self.hass)
        api = TionClient(
            session, user, password, min_update_interval_sec=interval, auth=auth_data
        )
        return await api.authorization

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return TionOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step user."""

        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})

            auth_data = await self._get_auth_data(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                DEFAULT_SCAN_INTERVAL,
            )

            if auth_data is None:
                errors["base"] = "invalid_auth"
            else:
                sha256_hash = hashlib.new("sha256")
                sha256_hash.update(user_input[CONF_USERNAME].encode())
                unique_id = f"{sha256_hash.hexdigest()}"

                await self.async_set_unique_id(unique_id)
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

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> ConfigFlowResult:
        """Attempt to import the existing configuration."""
        self._async_abort_entries_match(
            {CONF_USERNAME: import_config.get(CONF_USERNAME)}
        )
        return await self.async_step_user(import_config)

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
            auth_data = await self._get_auth_data(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                DEFAULT_SCAN_INTERVAL,
            )

            if auth_data is None:
                errors["base"] = "invalid_auth"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        AUTH_DATA: auth_data,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=""): str,
                    vol.Required(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )


class TionOptionsFlow(OptionsFlow):
    """Tion options flow handler."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Tion options flow."""
        self._entry_data = dict(config_entry.data)

    async def async_step_init(self, user_input=None):
        """Manage the options."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(CONF_SCAN_INTERVAL),
                    ): vol.Coerce(int),
                }
            ),
        )
