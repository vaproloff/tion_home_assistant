"""Adds config flow (UI flow) for Tion component."""

import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import TionClient
from .const import AUTH_DATA, DOMAIN

DEFAULT_SCAN_INTERVAL = 60

_LOGGER = logging.getLogger(__name__)


class TionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Tion config flow."""

    VERSION = 1

    def _check_auth(self, user, password, interval, auth_data=None) -> bool:
        session = async_create_clientsession(self.hass)
        api = TionClient(
            session, user, password, min_update_interval_sec=interval, auth=auth_data
        )
        return api.authorization

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step user."""
        self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})

        errors: dict[str, str] = {}
        if user_input is not None:
            sha256_hash = hashlib.new("sha256")
            sha256_hash.update(user_input[CONF_USERNAME].encode())
            sha256_hex = sha256_hash.hexdigest()

            try:
                interval = int(user_input.get(CONF_SCAN_INTERVAL))
            except ValueError:
                interval = DEFAULT_SCAN_INTERVAL
            except TypeError:
                interval = DEFAULT_SCAN_INTERVAL

            auth_data = await self.hass.async_add_executor_job(
                self._check_auth,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                interval,
            )

            if auth_data is None:
                errors["base"] = "invalid_auth"
            else:
                unique_id = f"{sha256_hex}"

                # Checks that the device is actually unique, otherwise abort
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_SCAN_INTERVAL: interval,
                        AUTH_DATA: auth_data,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=""): str,
                    vol.Required(CONF_PASSWORD, default=""): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=f"{DEFAULT_SCAN_INTERVAL}"
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> ConfigFlowResult:
        """Attempt to import the existing configuration."""
        self._async_abort_entries_match({CONF_USERNAME: import_config[CONF_USERNAME]})

        # return await self.async_step_user(import_config)
        errors: dict[str, str] = {}
        if import_config is not None:
            sha256_hash = hashlib.new("sha256")
            sha256_hash.update(import_config[CONF_USERNAME].encode())
            sha256_hex = sha256_hash.hexdigest()

            try:
                interval = int(import_config.get(CONF_SCAN_INTERVAL))
            except ValueError:
                interval = DEFAULT_SCAN_INTERVAL
            except TypeError:
                interval = DEFAULT_SCAN_INTERVAL

            auth_data = await self.hass.async_add_executor_job(
                self._check_auth,
                import_config[CONF_USERNAME],
                import_config[CONF_PASSWORD],
                interval,
            )

            if auth_data is None:
                errors["base"] = "invalid_auth"
            else:
                unique_id = f"{sha256_hex}"

                # Checks that the device is actually unique, otherwise abort
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=import_config[CONF_USERNAME],
                    data={
                        CONF_USERNAME: import_config[CONF_USERNAME],
                        CONF_PASSWORD: import_config[CONF_PASSWORD],
                        CONF_SCAN_INTERVAL: interval,
                        AUTH_DATA: auth_data,
                    },
                )
