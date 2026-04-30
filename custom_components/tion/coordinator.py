"""Coordinator for Tion integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

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
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class TionData:
    """Tion coordinator data."""

    locations: list[TionLocation]


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

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Tion",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> TionData:
        """Fetch data from Tion API."""
        try:
            return TionData(await self.client.get_locations())
        except TionAuthError as err:
            raise ConfigEntryAuthFailed from err
        except (TionApiError, TionConnectionError) as err:
            raise UpdateFailed(str(err)) from err

    def get_devices(self):
        """Get all devices from coordinator data."""
        return [
            device
            for location in self.data.locations
            for zone in location.zones
            for device in zone.devices
        ]

    def get_device(self, guid: str):
        """Get a device by guid from coordinator data."""
        for device in self.get_devices():
            if device.guid == guid:
                return device

        return None

    def get_device_zone(self, guid: str):
        """Get a device zone by device guid from coordinator data."""
        for location in self.data.locations:
            for zone in location.zones:
                for device in zone.devices:
                    if device.guid == guid:
                        return zone

        return None
