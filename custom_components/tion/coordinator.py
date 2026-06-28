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
from .const import BREEZER_TYPES, TionDeviceType
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

    def is_breezer_reachable(self, guid: str) -> bool:
        """Return whether a breezer is reachable through an online gateway.

        Breezers reach the cloud only through a MagicAir bound to the same
        hardware zone (``zone_hwid``), regardless of logical zone. When such a
        station is present, the breezer is reachable only if that station is
        online -- the breezer's own ``is_online`` freezes stale once the
        gateway drops. With no bound station in the snapshot, fall back to the
        breezer's own flag.
        """
        device = self.device(guid)
        if device is None:
            return False
        station_online = self._bound_station_online(device)
        if station_online is None:
            return bool(device.is_online)
        return station_online and bool(device.is_online)

    def _bound_station_online(self, breezer: TionZoneDevice) -> bool | None:
        """Return whether a MagicAir bound to the breezer's hw zone is online.

        ``None`` means no such station exists in the current snapshot, so the
        caller should fall back to the breezer's own ``is_online``.
        """
        hwid = breezer.zone_hwid
        if hwid is None:
            return None
        stations = [
            device
            for device in self.devices()
            if device.type == TionDeviceType.MAGIC_AIR and device.zone_hwid == hwid
        ]
        if not stations:
            return None
        return any(bool(station.is_online) for station in stations)

    def is_zone_reachable(self, zone_guid: str) -> bool:
        """Return whether a zone can be commanded through an online gateway.

        A zone is reachable when it has no MagicAir (cannot tell) or at least
        one of its MagicAir gateways is online.
        """
        for location in self.locations:
            for zone in location.zones:
                if zone.guid != zone_guid:
                    continue
                stations = [
                    device
                    for device in zone.devices
                    if device.type == TionDeviceType.MAGIC_AIR
                ]
                return not stations or any(
                    bool(station.is_online) for station in stations
                )
        return False


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
        self._log_fetched_breezers(data)
        self.apply_desired(data)
        return data

    def _log_fetched_breezers(self, data: TionData) -> None:
        """Log the raw cloud state per breezer, before optimistic overlays apply.

        Runs on the pristine fetched snapshot (before apply_desired overwrites
        device data with desired values), so it reflects what the cloud actually
        reported, identified by device name rather than guid.
        """
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        for device in data.devices():
            if device.type not in BREEZER_TYPES or not device.guid:
                continue
            breezer = device.data
            _LOGGER.debug(
                "%s: fetched cloud data: is_online=%s, reachable=%s, valid=%s, "
                "is_on=%s, speed=%s, speed_min_set=%s, speed_max_set=%s, "
                "t_set=%s, t_in=%s, t_out=%s, heater_enabled=%s, heater_mode=%s, "
                "heater_power=%s, gate=%s, filter_time_seconds=%s, "
                "filter_need_replace=%s",
                device.name,
                device.is_online,
                data.is_breezer_reachable(device.guid),
                device.valid,
                breezer.is_on,
                breezer.speed,
                breezer.speed_min_set,
                breezer.speed_max_set,
                breezer.t_set,
                breezer.t_in,
                breezer.t_out,
                breezer.heater_enabled,
                breezer.heater_mode,
                breezer.heater_power,
                breezer.gate,
                breezer.filter_time_seconds,
                breezer.filter_need_replace,
            )

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
