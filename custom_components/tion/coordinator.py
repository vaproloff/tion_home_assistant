"""The Tion account snapshot, pushed by TionCloud and re-read periodically."""

from datetime import datetime
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    TionAccount,
    TionApiError,
    TionAuthError,
    TionCloud,
    TionConnectionError,
)
from .const import DISCONNECT_GRACE, DOMAIN, REFRESH_INTERVAL

if TYPE_CHECKING:
    from .pid_manager import TionPidManager

_LOGGER = logging.getLogger(__name__)

type TionConfigEntry = ConfigEntry[TionCoordinator]


class TionCoordinator(DataUpdateCoordinator[TionAccount]):
    """Shares the account snapshot of one Tion cloud with the entities."""

    config_entry: TionConfigEntry
    # Set by async_setup_entry right after the coordinator.
    pid: TionPidManager

    def __init__(
        self, hass: HomeAssistant, config_entry: TionConfigEntry, cloud: TionCloud
    ) -> None:
        """Start from the cloud's current snapshot and follow its changes."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=REFRESH_INTERVAL,
            always_update=False,
        )
        self.cloud = cloud
        self.data = cloud.account
        # False once the live channel has been down for DISCONNECT_GRACE.
        self.channel_up = cloud.connected
        self._unsub_grace: CALLBACK_TYPE | None = None
        self._unsub_cloud = cloud.add_listener(self._handle_cloud_update)

    def device_available(self, device_id: str) -> bool:
        """Return True while the channel is up and the device and gateway online."""
        if not self.channel_up or (device := self.data.device(device_id)) is None:
            return False
        gateway = (
            self.data.device(device.parent_id) if device.parent_id is not None else None
        )
        return device.is_online and (gateway is None or gateway.is_online)

    @callback
    def _handle_cloud_update(self) -> None:
        # Not async_set_updated_data: it restarts the refresh timer, and pushes
        # arrive every few seconds, so the periodic refresh would never run.
        self.data = self.cloud.account
        if self.cloud.connected:
            self._cancel_grace()
            self.channel_up = True
        elif self.channel_up and self._unsub_grace is None:
            self._unsub_grace = async_call_later(
                self.hass, DISCONNECT_GRACE, self._grace_expired
            )
        if self.cloud.auth_error is not None:
            self.config_entry.async_start_reauth(self.hass)
        self.async_update_listeners()

    @callback
    def _grace_expired(self, _now: datetime) -> None:
        self._unsub_grace = None
        self.channel_up = False
        self.async_update_listeners()

    @callback
    def _cancel_grace(self) -> None:
        if self._unsub_grace is not None:
            self._unsub_grace()
            self._unsub_grace = None

    async def _async_update_data(self) -> TionAccount:
        """Re-read the structure: online flags change only there."""
        try:
            await self.cloud.async_refresh()
        except TionAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TionConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cloud_unavailable"
            ) from err
        except TionApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cloud_error",
                translation_placeholders={"message": str(err)},
            ) from err
        return self.cloud.account

    async def async_shutdown(self) -> None:
        """Stop following the cloud; it is stopped after this."""
        self._unsub_cloud()
        self._cancel_grace()
        await super().async_shutdown()
