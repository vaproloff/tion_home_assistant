"""The Tion API interaction module."""

import asyncio
import logging
from time import time
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import Heater, ZoneMode

_LOGGER = logging.getLogger(__name__)


class TionZoneModeAutoSet:
    """Tion zone mode auto set."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone mode auto set initialization."""
        self.co2 = data.get("co2")


class TionZoneMode:
    """Tion zone mode."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone mode initialization."""
        self.current = data.get("current")
        self.auto_set = TionZoneModeAutoSet(data.get("auto_set", {}))


class TionZoneDeviceData:
    """Tion zone device data."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone device data initialization."""
        self.co2 = data.get("co2")
        self.temperature = data.get("temperature")
        self.humidity = data.get("humidity")
        self.pm25 = data.get("pm25")
        self.backlight = data.get("backlight")
        self.sound_is_on = data.get("sound_is_on")
        self.is_on = data.get("is_on")
        self.data_valid = data.get("data_valid")
        self.heater_installed = data.get("heater_installed")
        self.heater_enabled = data.get("heater_enabled")
        self.heater_type = data.get("heater_type")
        self.heater_mode = data.get("heater_mode")
        self.heater_power = data.get("heater_power")
        self.speed = data.get("speed")
        self.speed_max_set = data.get("speed_max_set")
        self.speed_min_set = data.get("speed_min_set")
        self.speed_limit = data.get("speed_limit")
        self.t_in = data.get("t_in")
        self.t_set = data.get("t_set")
        self.t_out = data.get("t_out")
        self.gate = data.get("gate")
        self.filter_time_seconds = data.get("filter_time_seconds")
        self.filter_need_replace = data.get("filter_need_replace")


class TionZoneDevice:
    """Tion zone device."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone device initialization."""
        self.guid = data.get("guid")
        self.name = data.get("name")
        self.type = data.get("type")
        self.mac = data.get("mac")
        self.data = TionZoneDeviceData(data.get("data", {}))
        self.firmware = data.get("firmware")
        self.hardware = data.get("hardware")
        self.max_speed = data.get("max_speed")
        self.t_max = data.get("t_max")
        self.t_min = data.get("t_min")
        self.is_online = data.get("is_online")

    @property
    def valid(self) -> bool:
        """Return if device data valid."""
        if self.data.data_valid is not None:
            return self.data.data_valid

        return self.guid is not None


class TionZone:
    """Tion zone."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone data initialization."""
        self.guid = data.get("guid")
        self.name = data.get("name")
        self.mode = TionZoneMode(data.get("mode", {}))
        self.devices = [TionZoneDevice(device) for device in data.get("devices", [])]

    @property
    def valid(self) -> bool:
        """Return if zone data valid."""
        return self.guid is not None


class TionLocation:
    """Tion location class."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion location data initialization."""
        self.guid = data.get("guid")
        self.name = data.get("name")
        self.zones = [
            TionZone(zone)
            for zone in data.get("zones", [])
            if not zone.get("is_virtual")
        ]


class TionClient:
    """Tion API Client."""

    _API_ENDPOINT = "https://api2.magicair.tion.ru/"
    _AUTH_URL = "idsrv/oauth2/token"
    _LOCATION_URL = "location"
    _DEVICE_URL = "device"
    _ZONE_URL = "zone"
    _TASK_URL = "task"
    _CLIENT_ID = "cd594955-f5ba-4c20-9583-5990bb29f4ef"
    _CLIENT_SECRET = "syRxSrT77P"

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        min_update_interval_sec=10,
        auth=None,
    ) -> None:
        """Tion API client initialization."""
        self._session = session
        self._username = username
        self._password = password
        self._min_update_interval = min_update_interval_sec
        self._authorization = auth

        self._locations: list[TionLocation] = []
        self._auth_update_listeners = []
        self._last_update = 0
        self._temp_lock = asyncio.Lock()

    @property
    async def _headers(self):
        """Return headers for API request."""
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "ru-RU",
            "Authorization": self._authorization,
            "Connection": "Keep-Alive",
            "Content-Type": "application/json",
            "Host": "api2.magicair.tion.ru",
            "Origin": "https://magicair.tion.ru",
            "Referer": "https://magicair.tion.ru/dashboard/overview",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2486.0 Safari/537.36 Edge/13.10586",
        }

    @property
    async def authorization(self) -> str:
        """Return authorization data."""
        if self._authorization is None:
            if await self._get_authorization():
                return self._authorization

        elif await self._get_data():
            return self._authorization

        return None

    def add_update_listener(self, coro):
        """Add entry data update listener function."""
        self._auth_update_listeners.append(coro)

    async def _get_authorization(self):
        data = {
            "username": self._username,
            "password": self._password,
            "client_id": self._CLIENT_ID,
            "client_secret": self._CLIENT_SECRET,
            "grant_type": "password",
        }

        response = await self._session.post(
            url=f"{self._API_ENDPOINT}{self._AUTH_URL}", data=data, timeout=10
        )

        if response.status == 200:
            response = await response.json()
            self._authorization = f"{response['token_type']} {response['access_token']}"

            _LOGGER.info("TionClient: got new authorization token")

            for coro in self._auth_update_listeners:
                await coro(
                    username=self._username,
                    password=self._password,
                    scan_interval=self._min_update_interval,
                    auth=self._authorization,
                )

            return True

        _LOGGER.error(
            "TionClient: response while getting token: status code: %s, content:\n%s",
            response.status,
            await response.json(),
        )
        return False

    async def _get_data(self):
        response = await self._session.get(
            url=f"{self._API_ENDPOINT}{self._LOCATION_URL}",
            headers=await self._headers,
            timeout=10,
        )

        if response.status == 200:
            self._locations = [
                TionLocation(location) for location in await response.json()
            ]
            self._last_update = time()

            _LOGGER.debug(
                "TionClient: location data has been updated (%s)", self._last_update
            )
            return True

        if response.status == 401:
            _LOGGER.info("TionClient: need to get new authorization")
            if await self._get_authorization():
                return await self._get_data()

            _LOGGER.error("TionClient: authorization failed!")
        else:
            _LOGGER.error(
                "TionClient: response while getting location data: status code: %s, content:\n%s",
                response.status,
                await response.json(),
            )

        return False

    async def get_location_data(self, force=False) -> bool:
        """Get locations data from Tion API."""
        async with self._temp_lock:
            if not force and (time() - self._last_update) < self._min_update_interval:
                _LOGGER.debug(
                    "TionClient: location data already updated recently. Skipping request"
                )
                return self._locations is not None

            return await self._get_data()

    async def get_zone(self, guid: str, force=False) -> TionZone | None:
        """Get zone data by guid from Tion API."""
        if await self.get_location_data(force=force):
            for location in self._locations:
                for zone_data in location.zones:
                    if zone_data.guid == guid:
                        return zone_data

        return None

    async def get_device(self, guid: str, force=False) -> TionZoneDevice | None:
        """Get device data by guid from Tion API."""
        if await self.get_location_data(force=force):
            for location in self._locations:
                for zone in location.zones:
                    for device in zone.devices:
                        if device.guid == guid:
                            return device

        return None

    async def get_device_zone(self, guid: str, force=False) -> TionZone | None:
        """Get device zone data by device guid from Tion API."""
        if await self.get_location_data(force=force):
            for location in self._locations:
                for zone in location.zones:
                    for device in zone.devices:
                        if device.guid == guid:
                            return zone

        return None

    async def get_devices(self, force=False) -> list[TionZoneDevice]:
        """Get all devices data from Tion API."""
        if await self.get_location_data(force=force):
            return [
                device
                for location in self._locations
                for zone in location.zones
                for device in zone.devices
            ]

        return []

    async def send_breezer(
        self,
        guid: str,
        is_on: bool,
        t_set: int,
        speed: int,
        speed_min_set: int,
        speed_max_set: int,
        heater_enabled: bool | None = None,
        heater_mode: Heater | None = None,
        gate: int | None = None,
    ):
        """Send new breezer data to API."""
        url = f"{self._API_ENDPOINT}{self._DEVICE_URL}/{guid}/mode"
        data = {
            "is_on": is_on,
            "heater_enabled": heater_enabled,
            "heater_mode": heater_mode,
            "t_set": t_set,
            "speed": speed,
            "speed_min_set": speed_min_set,
            "speed_max_set": speed_max_set,
            "gate": gate,
        }

        return await self._send(url, data)

    async def send_zone(self, guid: str, mode: ZoneMode, co2: int):
        """Send new zone data to API."""
        url = f"{self._API_ENDPOINT}{self._ZONE_URL}/{guid}/mode"
        data = {
            "mode": mode,
            "co2": co2,
        }

        return await self._send(url, data)

    async def send_settings(self, guid: str, data: dict[str, Any]):
        """Send new zone data to API."""
        url = f"{self._API_ENDPOINT}{self._DEVICE_URL}/{guid}/settings"

        return await self._send(url, data)

    async def _send(self, url: str, data: dict[str, Any]):
        response = await self._session.post(
            url=url,
            json=data,
            headers=await self._headers,
            timeout=10,
        )

        response = await response.json()
        if response["status"] != "queued":
            _LOGGER.error(
                "TionClient: parameters set %s: %s",
                response["status"],
                response["description"],
            )
            return False

        return await self._wait_for_task(response["task_id"])

    async def _wait_for_task(self, task_id: str, max_time: int = 5) -> bool:
        """Wait for task with defined task_id been completed."""
        DELAY = 0.5
        start_time = asyncio.get_event_loop().time()
        while True:
            try:
                elapsed_time = asyncio.get_event_loop().time() - start_time
                if elapsed_time >= max_time:
                    _LOGGER.warning(
                        "TionClient: timeout of %s seconds reached while waiting for a task",
                        max_time,
                    )
                    return False

                response = await self._session.get(
                    url=f"{self._API_ENDPOINT}{self._TASK_URL}/{task_id}",
                    headers=await self._headers,
                    timeout=10,
                )

                if response.status == 200:
                    response = await response.json()
                    if response["status"] == "completed":
                        return await self.get_location_data(force=True)

                    await asyncio.sleep(DELAY)
                else:
                    _LOGGER.warning(
                        "TionClient: bad response code %s while waiting for a task, content:\n%s",
                        response.status,
                        response.text(),
                    )
                    return False

            except (ClientError, TimeoutError) as e:
                _LOGGER.error("TionClient: exception in waiting for a task:\n%s", e)
                await asyncio.sleep(DELAY)
