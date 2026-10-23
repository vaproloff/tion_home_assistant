"""The Tion account snapshot, pushed by TionCloud and re-read periodically."""

from datetime import datetime
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    TionAccount,
    TionApiError,
    TionAuthError,
    TionAuthStaleError,
    TionCloud,
    TionConnectionError,
)
from .const import (
    DATA_START_FAILURES,
    DISCONNECT_GRACE,
    DOMAIN,
    REAUTH_DATA_STALE,
    REAUTH_STEP_STALE,
    REFRESH_INTERVAL,
)

if TYPE_CHECKING:
    from .pid_manager import TionPidManager

_LOGGER = logging.getLogger(__name__)

type TionConfigEntry = ConfigEntry[TionCoordinator]


@callback
def async_abort_reauth_flows(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Abort the entry's open reauth flows."""
    for flow in hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"entry_id": entry.entry_id, "source": SOURCE_REAUTH},
    ):
        hass.config_entries.flow.async_abort(flow["flow_id"])


@callback
def async_open_reauth(hass: HomeAssistant, entry: ConfigEntry, *, stale: bool) -> None:
    """Open the sign-in form for the reason; a refusal replaces an outage form."""
    if not stale:
        for flow in hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id, "source": SOURCE_REAUTH},
        ):
            # A flow past the first form is the user's work in progress.
            if flow["step_id"] == REAUTH_STEP_STALE:
                hass.config_entries.flow.async_abort(flow["flow_id"])
    entry.async_start_reauth(hass, data={REAUTH_DATA_STALE: True} if stale else None)


@callback
def async_forget_failed_starts(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Drop the entry's failed-start record; return whether there was one."""
    return hass.data.get(DATA_START_FAILURES, {}).pop(entry.entry_id, None) is not None


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
        self._reauth_open = False
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
        self._async_sync_reauth()
        self.async_update_listeners()

    @callback
    def _async_sync_reauth(self) -> None:
        """Open or close the reauth flow to match the cloud's auth_error."""
        if self.cloud.auth_error is not None:
            self.async_request_reauth()
        else:
            self._async_resolve_reauth()

    @callback
    def async_request_reauth(self) -> None:
        """Ask the user to sign in again; a refusal replaces an open outage form."""
        # A reload after a successful reauth leaves the old coordinator pushing
        # the old auth_error, and a new flow would outlive the reload.
        if self.config_entry.state is ConfigEntryState.UNLOAD_IN_PROGRESS:
            return
        self._reauth_open = True
        async_open_reauth(
            self.hass,
            self.config_entry,
            stale=isinstance(self.cloud.auth_error, TionAuthStaleError),
        )

    @callback
    def _async_resolve_reauth(self) -> None:
        """Close the reauth flows this coordinator opened."""
        # HA itself aborts reauth flows only on reload and removal of the entry.
        if not self._reauth_open:
            return
        self._reauth_open = False
        async_abort_reauth_flows(self.hass, self.config_entry)

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
            self.async_request_reauth()
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TionConnectionError as err:
            self._async_sync_reauth()
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cloud_unavailable"
            ) from err
        except TionApiError as err:
            self._async_sync_reauth()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cloud_error",
                translation_placeholders={"message": str(err)},
            ) from err
        self._async_sync_reauth()
        return self.cloud.account

    async def async_shutdown(self) -> None:
        """Stop following the cloud; it is stopped after this."""
        self._unsub_cloud()
        self._cancel_grace()
        await super().async_shutdown()
