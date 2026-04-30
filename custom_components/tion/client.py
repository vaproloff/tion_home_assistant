"""The Tion API interaction module."""

import asyncio
from collections.abc import Awaitable, Callable
from json import JSONDecodeError
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ContentTypeError

from .const import Heater, ZoneMode

_LOGGER = logging.getLogger(__name__)


class TionError(Exception):
    """Base Tion client error."""


class TionAuthError(TionError):
    """Tion authentication error."""


class TionConnectionError(TionError):
    """Tion connection error."""


class TionApiError(TionError):
    """Unexpected Tion API error."""


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
        self._auth_update_listeners: list[Callable[[str], Awaitable[None]]] = []

    @property
    def authorization(self) -> str | None:
        """Return authorization data."""
        return self._authorization

    def _headers(self) -> dict[str, str]:
        """Return headers for API request."""
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "ru-RU",
            "Authorization": self._authorization or "",
            "Connection": "Keep-Alive",
            "Content-Type": "application/json",
            "Host": "api2.magicair.tion.ru",
            "Origin": "https://magicair.tion.ru",
            "Referer": "https://magicair.tion.ru/dashboard/overview",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2486.0 Safari/537.36 Edge/13.10586",
        }

    def add_update_listener(self, coro: Callable[[str], Awaitable[None]]) -> None:
        """Add entry data update listener function."""
        self._auth_update_listeners.append(coro)

    async def async_validate_auth(self) -> str:
        """Validate credentials and return authorization data."""
        await self.async_get_authorization()
        await self.get_locations()

        if self._authorization is None:
            raise TionAuthError("Tion authorization failed")

        return self._authorization

    async def async_get_authorization(self) -> str:
        """Get a new authorization token."""
        data = {
            "username": self._username,
            "password": self._password,
            "client_id": self._CLIENT_ID,
            "client_secret": self._CLIENT_SECRET,
            "grant_type": "password",
        }

        response = await self._request(
            "post",
            self._AUTH_URL,
            data=data,
            auth_required=False,
            auth_request=True,
        )

        try:
            self._authorization = f"{response['token_type']} {response['access_token']}"
        except KeyError as err:
            raise TionApiError(
                "Tion API response did not contain an access token"
            ) from err

        _LOGGER.debug("TionClient: got new authorization token")

        for coro in self._auth_update_listeners:
            await coro(self._authorization)

        return self._authorization

    async def _request(
        self,
        method: str,
        url_path: str,
        *,
        auth_required: bool = True,
        auth_request: bool = False,
        retry_auth: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Make a request to the Tion API."""
        if auth_required and self._authorization is None:
            await self.async_get_authorization()

        try:
            async with self._session.request(
                method,
                url=f"{self._API_ENDPOINT}{url_path}",
                headers=self._headers() if auth_required else None,
                timeout=10,
                **kwargs,
            ) as response:
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except (ContentTypeError, JSONDecodeError, ValueError) as err:
                    if status >= 400:
                        data = {}
                    else:
                        raise TionApiError(
                            f"Tion API returned a non-JSON response with status {status}"
                        ) from err
        except (ClientError, TimeoutError) as err:
            raise TionConnectionError("Error communicating with Tion API") from err

        if status == 401 and auth_required:
            if retry_auth:
                _LOGGER.debug("TionClient: need to get new authorization")
                await self.async_get_authorization()
                return await self._request(
                    method,
                    url_path,
                    auth_required=auth_required,
                    auth_request=auth_request,
                    retry_auth=False,
                    **kwargs,
                )

            raise TionAuthError("Tion authorization failed")

        if auth_request and status in (400, 401, 403):
            raise TionAuthError("Invalid Tion credentials")

        if status >= 500:
            raise TionConnectionError(f"Tion API returned status {status}")

        if status >= 400:
            raise TionApiError(f"Tion API returned status {status}")

        return data

    async def get_locations(self) -> list[TionLocation]:
        """Get locations data from Tion API."""
        response = await self._request("get", self._LOCATION_URL)
        if not isinstance(response, list):
            raise TionApiError("Tion API returned invalid location data")

        self._locations = [TionLocation(location) for location in response]
        _LOGGER.debug("TionClient: location data has been updated")
        return self._locations

    async def get_zone(self, guid: str) -> TionZone | None:
        """Get zone data by guid from Tion API."""
        await self.get_locations()
        for location in self._locations:
            for zone_data in location.zones:
                if zone_data.guid == guid:
                    return zone_data

        return None

    async def get_device(self, guid: str) -> TionZoneDevice | None:
        """Get device data by guid from Tion API."""
        await self.get_locations()
        for location in self._locations:
            for zone in location.zones:
                for device in zone.devices:
                    if device.guid == guid:
                        return device

        return None

    async def get_device_zone(self, guid: str) -> TionZone | None:
        """Get device zone data by device guid from Tion API."""
        await self.get_locations()
        for location in self._locations:
            for zone in location.zones:
                for device in zone.devices:
                    if device.guid == guid:
                        return zone

        return None

    async def get_devices(self) -> list[TionZoneDevice]:
        """Get all devices data from Tion API."""
        await self.get_locations()
        return [
            device
            for location in self._locations
            for zone in location.zones
            for device in zone.devices
        ]

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

        return await self._send(f"{self._DEVICE_URL}/{guid}/mode", data)

    async def send_zone(self, guid: str, mode: ZoneMode, co2: int):
        """Send new zone data to API."""
        data = {
            "mode": mode,
            "co2": co2,
        }

        return await self._send(f"{self._ZONE_URL}/{guid}/mode", data)

    async def send_settings(self, guid: str, data: dict[str, Any]):
        """Send new settings data to API."""
        return await self._send(f"{self._DEVICE_URL}/{guid}/settings", data)

    async def _send(self, url_path: str, data: dict[str, Any]) -> bool:
        response = await self._request("post", url_path, json=data)
        if response.get("status") != "queued":
            raise TionApiError(
                "Tion API did not queue the command: "
                f"{response.get('status')} {response.get('description')}"
            )

        try:
            task_id = response["task_id"]
        except KeyError as err:
            raise TionApiError("Tion API response did not contain a task id") from err

        return await self._wait_for_task(task_id)

    async def _wait_for_task(self, task_id: str, max_time: int = 5) -> bool:
        """Wait for task with defined task_id been completed."""
        delay = 0.5
        start_time = asyncio.get_event_loop().time()
        while True:
            elapsed_time = asyncio.get_event_loop().time() - start_time
            if elapsed_time >= max_time:
                raise TionApiError(
                    f"Timed out after {max_time} seconds waiting for Tion task"
                )

            response = await self._request("get", f"{self._TASK_URL}/{task_id}")
            task_status = response.get("status")
            if task_status in ("completed", "delivered"):
                await self.get_locations()
                return True

            if task_status not in ("queued", "processing", "in_progress"):
                raise TionApiError(f"Tion task ended with status {task_status}")

            await asyncio.sleep(delay)
