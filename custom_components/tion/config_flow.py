"""Adds config flow (UI flow) for Tion component."""

import hashlib
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import (
    CONF_FILE_PATH,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .tion_api import TionApi

DEFAULT_SCAN_INTERVAL = 60


class TionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Tion config flow."""

    VERSION = 1

    def _check_auth(self, user, password, interval, auth_fname) -> bool:
        api = TionApi(
            user, password, min_update_interval_sec=interval, auth_fname=auth_fname
        )

        return api.get_data()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            sha256_hash = hashlib.new("sha256")
            sha256_hash.update(user_input[CONF_USERNAME].encode())
            sha256_hex = sha256_hash.hexdigest()

            auth_fname = f"tion_auth-{sha256_hex}"

            try:
                interval = int(user_input[CONF_SCAN_INTERVAL])
            except ValueError:
                interval = DEFAULT_SCAN_INTERVAL

            auth = await self.hass.async_add_executor_job(
                self._check_auth,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                interval,
                auth_fname,
            )

            if auth is False:
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
                        CONF_FILE_PATH: auth_fname,
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
