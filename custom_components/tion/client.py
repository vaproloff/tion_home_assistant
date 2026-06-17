"""The Tion API interaction module."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TionApiProfile:
    """Connection profile for one Tion cloud endpoint."""

    name: str
    endpoint: str
    auth_url: str
    location_url: str
    device_url: str
    zone_url: str
    task_url: str
    client_id: str
    client_secret: str
    grant_type: str
    host: str
    base_headers: dict[str, str]
    scope: str | None = None
    timeout: int = 10


API_PROFILE = TionApiProfile(
    name="api",
    endpoint="https://api.magicair.tion.ru/",
    auth_url="idsrv/connect/token",
    location_url="Location",
    device_url="device",
    zone_url="zone",
    task_url="task",
    client_id="a750d720-e146-47b0-b414-35e3b1dd7862",
    client_secret="DTT2jJnY3k2H2GyZ",
    grant_type="extended",
    scope="offline_access ma-account ma-device ma-firmware",
    host="api.magicair.tion.ru",
    base_headers={
        "Accept": "application/json",
        "Accept-Language": "ru-RU;q=1, en-RU;q=0.9",
        "Connection": "Keep-Alive",
        "Host": "api.magicair.tion.ru",
    },
)

API2_PROFILE = TionApiProfile(
    name="api2",
    endpoint="https://api2.magicair.tion.ru/",
    auth_url="idsrv/oauth2/token",
    location_url="location",
    device_url="device",
    zone_url="zone",
    task_url="task",
    client_id="cd594955-f5ba-4c20-9583-5990bb29f4ef",
    client_secret="syRxSrT77P",
    grant_type="password",
    scope=None,
    host="api2.magicair.tion.ru",
    base_headers={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU",
        "Connection": "Keep-Alive",
        "Host": "api2.magicair.tion.ru",
        "Origin": "https://magicair.tion.ru",
        "Referer": "https://magicair.tion.ru/dashboard/overview",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/46.0.2486.0 Safari/537.36 Edge/13.10586"
        ),
    },
)

PROFILES: list[TionApiProfile] = [API_PROFILE, API2_PROFILE]
PROFILES_BY_NAME: dict[str, TionApiProfile] = {p.name: p for p in PROFILES}
DEFAULT_PROFILE = API_PROFILE


class TionClient:
    """Tion API Client."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        min_update_interval_sec=10,
        auth: str | dict[str, str | None] | None = None,
        active_profile: str | None = None,
    ) -> None:
        """Tion API client initialization."""
        self._session = session
        self._username = username
        self._password = password
        self._min_update_interval = min_update_interval_sec
        self._profiles = PROFILES
        self._authorization = self._normalize_auth(auth)
        self._active = self._resolve_active_index(active_profile)

        self._locations: list[TionLocation] = []
        self._auth_update_listeners: list[Callable[[str, str], Awaitable[None]]] = []
        self._active_profile_listeners: list[Callable[[str], Awaitable[None]]] = []

    @staticmethod
    def _normalize_auth(
        auth: str | dict[str, str | None] | None,
    ) -> dict[str, str | None]:
        """Coerce stored auth (legacy str / dict / None) into a per-profile dict."""
        if isinstance(auth, str):
            return {p.name: (auth if p is DEFAULT_PROFILE else None) for p in PROFILES}
        if isinstance(auth, dict):
            return {p.name: auth.get(p.name) for p in PROFILES}
        return {p.name: None for p in PROFILES}

    def _resolve_active_index(self, active_profile: str | None) -> int:
        """Map a persisted profile name to its index, defaulting to api."""
        profile = PROFILES_BY_NAME.get(active_profile or "", DEFAULT_PROFILE)
        return self._profiles.index(profile)

    @property
    def authorization(self) -> dict[str, str | None]:
        """Return per-profile authorization data."""
        return dict(self._authorization)

    @property
    def active_profile(self) -> str:
        """Return the name of the currently active profile."""
        return self._profiles[self._active].name

    def _headers(self, profile: TionApiProfile) -> dict[str, str]:
        """Return headers for a request on the given profile."""
        return {
            **profile.base_headers,
            "Authorization": self._authorization.get(profile.name) or "",
        }

    def add_update_listener(self, coro: Callable[[str, str], Awaitable[None]]) -> None:
        """Add a listener notified as (profile_name, token) on re-auth."""
        self._auth_update_listeners.append(coro)

    def add_active_profile_listener(
        self, coro: Callable[[str], Awaitable[None]]
    ) -> None:
        """Add a listener notified with the profile name on a failover switch."""
        self._active_profile_listeners.append(coro)

    async def async_validate_auth(self) -> dict[str, str | None]:
        """Validate credentials against the default profile and return auth data."""
        await self.async_get_authorization(DEFAULT_PROFILE)
        await self.get_locations()

        if not self._authorization.get(DEFAULT_PROFILE.name):
            raise TionAuthError("Tion authorization failed")

        return self.authorization

    async def async_get_authorization(
        self, profile: TionApiProfile | None = None
    ) -> str:
        """Get a new authorization token for the given (or active) profile."""
        profile = profile or self._profiles[self._active]
        data = {
            "username": self._username,
            "password": self._password,
            "client_id": profile.client_id,
            "client_secret": profile.client_secret,
            "grant_type": profile.grant_type,
        }
        if profile.scope:
            data["scope"] = profile.scope

        response = await self._request(
            "post",
            profile.auth_url,
            data=data,
            auth_required=False,
            auth_request=True,
            profile=profile,
        )

        try:
            token = f"{response['token_type']} {response['access_token']}"
        except KeyError as err:
            raise TionApiError(
                "Tion API response did not contain an access token"
            ) from err

        self._authorization[profile.name] = token
        _LOGGER.debug("TionClient: got new authorization for profile %s", profile.name)

        for coro in self._auth_update_listeners:
            await coro(profile.name, token)

        return token

    async def _request(
        self,
        method: str,
        url_path: str | Callable[[TionApiProfile], str],
        *,
        auth_required: bool = True,
        auth_request: bool = False,
        retry_auth: bool = True,
        profile: TionApiProfile | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make a request, failing over between profiles on connection errors."""
        if profile is not None:
            return await self._request_profile(
                profile,
                method,
                url_path,
                auth_required=auth_required,
                auth_request=auth_request,
                retry_auth=retry_auth,
                **kwargs,
            )

        last_error: TionConnectionError | None = None
        index = self._active
        for _attempt in range(len(self._profiles)):
            target = self._profiles[index]
            try:
                result = await self._request_profile(
                    target,
                    method,
                    url_path,
                    auth_required=auth_required,
                    auth_request=auth_request,
                    retry_auth=retry_auth,
                    **kwargs,
                )
            except TionConnectionError as err:
                last_error = err
                index = (index + 1) % len(self._profiles)
                continue
            await self._set_active(index)
            return result

        if last_error is not None:
            raise last_error
        raise TionConnectionError("Error communicating with Tion API")

    async def _set_active(self, index: int) -> None:
        """Switch the active profile and notify listeners on change."""
        if index == self._active:
            return

        self._active = index
        name = self._profiles[index].name
        _LOGGER.warning(
            "TionClient: switched to profile %s after a connection failure", name
        )
        for coro in self._active_profile_listeners:
            await coro(name)

    async def _request_profile(
        self,
        profile: TionApiProfile,
        method: str,
        url_path: str | Callable[[TionApiProfile], str],
        *,
        auth_required: bool = True,
        auth_request: bool = False,
        retry_auth: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Make a single request pinned to one profile (no failover)."""
        if auth_required and not self._authorization.get(profile.name):
            await self.async_get_authorization(profile)

        path = url_path(profile) if callable(url_path) else url_path
        try:
            async with self._session.request(
                method,
                url=f"{profile.endpoint}{path}",
                headers=self._headers(profile),
                timeout=profile.timeout,
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
                await self.async_get_authorization(profile)
                return await self._request_profile(
                    profile,
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
        response = await self._request("get", lambda profile: profile.location_url)
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

        device_url = self._profiles[self._active].device_url
        return await self._send(f"{device_url}/{guid}/mode", data)

    async def send_zone(self, guid: str, mode: ZoneMode, co2: int):
        """Send new zone data to API."""
        data = {
            "mode": mode,
            "co2": co2,
        }

        zone_url = self._profiles[self._active].zone_url
        return await self._send(f"{zone_url}/{guid}/mode", data)

    async def send_settings(self, guid: str, data: dict[str, Any]):
        """Send new settings data to API."""
        device_url = self._profiles[self._active].device_url
        return await self._send(f"{device_url}/{guid}/settings", data)

    async def _send(self, url_path: str, data: dict[str, Any]) -> bool:
        response = await self._request("post", url_path, json=data)
        served = self._profiles[self._active]
        if response.get("status") != "queued":
            raise TionApiError(
                "Tion API did not queue the command: "
                f"{response.get('status')} {response.get('description')}"
            )

        try:
            task_id = response["task_id"]
        except KeyError as err:
            raise TionApiError("Tion API response did not contain a task id") from err

        return await self._wait_for_task(task_id, profile=served)

    async def _wait_for_task(
        self, task_id: str, *, profile: TionApiProfile, max_time: int = 5
    ) -> bool:
        """Wait for the task to complete, pinned to the POST's profile."""
        delay = 0.5
        start_time = asyncio.get_event_loop().time()
        while True:
            elapsed_time = asyncio.get_event_loop().time() - start_time
            if elapsed_time >= max_time:
                raise TionApiError(
                    f"Timed out after {max_time} seconds waiting for Tion task"
                )

            response = await self._request(
                "get", f"{profile.task_url}/{task_id}", profile=profile
            )
            task_status = response.get("status")
            if task_status == "completed":
                return True

            await asyncio.sleep(delay)
