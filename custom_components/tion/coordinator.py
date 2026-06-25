"""Coordinator for Tion integration."""

import asyncio
from collections.abc import Awaitable
from contextlib import asynccontextmanager
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
from .reconciler import TionReconciler

_LOGGER = logging.getLogger(__name__)

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
        self.reconciler = TionReconciler(self)
        self._current_command_started_at: float | None = None
        self._last_command_completed_at: float | None = None
        self._settings_locks: dict[str, asyncio.Lock] = {}

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Tion",
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )

    @asynccontextmanager
    async def async_settings_command(self, guid: str):
        """Serialize settings (backlight/sound) writes for one device.

        Settings live on a separate endpoint from the breezer/zone payload the
        reconciler drives, so they keep their own lightweight lock.
        """
        lock = self._settings_locks.setdefault(guid, asyncio.Lock())
        async with lock:
            yield

    def _command_invalidates_fetch(self, request_started_at: float) -> bool:
        """Return whether a command makes a fetch started at this time stale.

        True when a command is in-flight, or one completed after the fetch
        began, so the fetched snapshot may predate the command's effect.
        """
        return self._current_command_started_at is not None or (
            self._last_command_completed_at is not None
            and request_started_at < self._last_command_completed_at
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
            _LOGGER.debug("Ignoring stale Tion location data")
            return self.data

        data = TionData(locations)
        self.apply_desired(data)
        return data

    def apply_desired(self, data: TionData) -> None:
        """Recompute active PID desired state, then reconcile toward all desired.

        This is the single pipeline that turns desired state into background
        commands: local PID writes its fields first, so a just-changed input
        (e.g. an auto-speed limit) is reflected in the very same reconcile pass,
        then the reconciler drives cloud state toward all desired state. Every
        writer that wants to push state immediately must go through here rather
        than calling ``reconcile`` directly, otherwise a PID-driven breezer
        would dispatch a stale speed and need a second command a cycle later.
        """
        if self.pid_manager.has_active_pid():
            self.pid_manager.write_all(data)
        self.reconciler.reconcile(data)

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
