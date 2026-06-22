"""Coordinator for Tion integration."""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    TionApiError,
    TionAuthError,
    TionClient,
    TionConnectionError,
    TionLocation,
    TionZone,
    TionZoneDevice,
)

_LOGGER = logging.getLogger(__name__)
POST_COMMAND_STALE_GRACE = 10.0

if TYPE_CHECKING:
    from .pid_manager import TionPidManager


@dataclass
class TionData:
    """Tion coordinator data."""

    locations: list[TionLocation]

    def devices(self) -> list[TionZoneDevice]:
        """Return all devices across all locations and zones."""
        return [
            device
            for location in self.locations
            for zone in location.zones
            for device in zone.devices
        ]

    def device(self, guid: str) -> TionZoneDevice | None:
        """Return the device with the given guid, or None."""
        return next((device for device in self.devices() if device.guid == guid), None)

    def zone(self, guid: str) -> TionZone | None:
        """Return the zone containing the device with the given guid, or None."""
        for location in self.locations:
            for zone in location.zones:
                if any(device.guid == guid for device in zone.devices):
                    return zone

        return None


class TionDataUpdateCoordinator(DataUpdateCoordinator[TionData]):
    """Class to manage fetching Tion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TionClient,
        scan_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.pid_manager: TionPidManager
        self._current_command_started_at: float | None = None
        self._last_command_completed_at: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Tion",
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )

    def _command_invalidates_fetch(self, request_started_at: float) -> bool:
        """Return whether a command makes a fetch started at this time stale.

        True when a command is in-flight, or one completed after the fetch
        began, so the fetched snapshot may predate the command's effect. The
        Tion cloud can also keep returning the pre-command snapshot briefly
        after a task reports completion, so keep optimistic local state during
        that propagation window.
        """
        if self._current_command_started_at is not None:
            return True
        if self._last_command_completed_at is None:
            return False
        return (
            request_started_at < self._last_command_completed_at
            or request_started_at - self._last_command_completed_at
            < POST_COMMAND_STALE_GRACE
        )

    async def _async_update_data(self) -> TionData:
        """Fetch data from Tion API."""
        request_started_at = self.hass.loop.time()
        try:
            locations = await self.client.get_locations()
        except TionAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (TionApiError, TionConnectionError) as err:
            raise UpdateFailed(str(err)) from err

        if self.data is not None and self._command_invalidates_fetch(
            request_started_at
        ):
            _LOGGER.debug(
                "Ignoring stale Tion location data: request_started_at=%s, "
                "last_command_completed_at=%s",
                request_started_at,
                self._last_command_completed_at,
            )
            return self.data

        data = TionData(locations)

        if self.pid_manager.has_active_pid():
            for intent in self.pid_manager.plan_all(data):
                # Reflect the command optimistically on the published snapshot,
                # then dispatch the network send in the background. If that send
                # later fails the snapshot is briefly ahead of reality; the next
                # poll reconciles it. Do not add a rollback here.
                intent.apply(data)
                self.pid_manager.schedule_intent(intent)

        return data

    async def _async_send_command(
        self,
        command: Awaitable[bool],
        *,
        request_refresh: bool = True,
        track_stale: bool = True,
    ) -> bool:
        """Send a command and refresh coordinator data after it succeeds."""
        command_started_at = self.hass.loop.time()
        if track_stale:
            self._current_command_started_at = command_started_at
        try:
            result = await command
            if track_stale:
                self._last_command_completed_at = self.hass.loop.time()
        finally:
            if track_stale and self._current_command_started_at == command_started_at:
                self._current_command_started_at = None

        if request_refresh:
            await self.async_request_refresh()

        return result

    async def async_send_breezer(
        self, *, request_refresh: bool = True, track_stale: bool = True, **kwargs: Any
    ) -> bool:
        """Send new breezer data to API."""
        return await self._async_send_command(
            self.client.send_breezer(**kwargs),
            request_refresh=request_refresh,
            track_stale=track_stale,
        )

    async def async_send_zone(
        self, *, request_refresh: bool = True, track_stale: bool = True, **kwargs: Any
    ) -> bool:
        """Send new zone data to API."""
        return await self._async_send_command(
            self.client.send_zone(**kwargs),
            request_refresh=request_refresh,
            track_stale=track_stale,
        )

    async def async_send_settings(
        self, *, request_refresh: bool = True, **kwargs: Any
    ) -> bool:
        """Send new settings data to API."""
        return await self._async_send_command(
            self.client.send_settings(**kwargs), request_refresh=request_refresh
        )

    def get_devices(self) -> list[TionZoneDevice]:
        """Get all devices from coordinator data."""
        return self.data.devices()

    def get_device(self, guid: str) -> TionZoneDevice | None:
        """Get a device by guid from coordinator data."""
        return self.data.device(guid)

    def get_device_zone(self, guid: str) -> TionZone | None:
        """Get a device zone by device guid from coordinator data."""
        return self.data.zone(guid)
