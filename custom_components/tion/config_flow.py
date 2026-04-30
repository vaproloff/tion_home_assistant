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
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import TionApiError, TionAuthError, TionClient, TionConnectionError
from .const import AUTH_DATA, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


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
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10)),
                }
            ),
        )
