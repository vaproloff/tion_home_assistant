"""The Tion API interaction module."""

import logging
from time import sleep, time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)


class TionZoneModeAutoSet:
    """Tion zone mode auto set."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone mode auto set initialization."""
        self.co2 = data.get("co2")
        self.temperature = data.get("temperature")
        self.humidity = data.get("humidity")
        self.noise = data.get("noise")
        self.pm25 = data.get("pm25")
        self.pm10 = data.get("pm10")


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
        self.pm10 = data.get("pm10")
        self.pm1 = data.get("pm1")
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
        self.is_online = data.get("is_online")
        self.data = TionZoneDeviceData(data.get("data", {}))
        self.firmware = data.get("firmware")
        self.hardware = data.get("hardware")
        self.max_speed = data.get("max_speed")
        self.t_max = data.get("t_max")
        self.t_min = data.get("t_min")

    @property
    def valid(self) -> bool:
        """Return if device data valid."""
        if self.data.data_valid is None:
            return self.guid is not None

        return self.guid is not None and self.data.data_valid


class TionZone:
    """Tion zone."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Tion zone data initialization."""
        self.guid = data.get("guid")
        self.name = data.get("name")
        self.is_virtual = data.get("is_virtual")
        self.mode = TionZoneMode(data.get("mode", {}))
        self.hw_id = data.get("hw_id")
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
        self.unique_key = data.get("unique_key")
        self.zones = [TionZone(zone) for zone in data.get("zones", [])]


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
        email: str,
        password: str,
        min_update_interval_sec=10,
        auth=None,
    ) -> None:
        """Tion API client initialization."""
        self._email = email
        self._password = password
        self._min_update_interval = min_update_interval_sec
        self._authorization = auth

        self._locations: list[TionLocation] = []
        self._auth_update_listeners = []
        self._last_update = 0

        self.get_location_data()

    @property
    def headers(self):
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
    def authorization(self) -> str:
        """Return authorization data."""
        return self._authorization

    def add_entry_data_updater(self, coro):
        """Add entry data update listener function."""
        self._auth_update_listeners.append(coro)

    def _get_authorization(self):
        data = {
            "username": self._email,
            "password": self._password,
            "client_id": self._CLIENT_ID,
            "client_secret": self._CLIENT_SECRET,
            "grant_type": "password",
        }
        try:
            response = requests.post(
                f"{self._API_ENDPOINT}{self._AUTH_URL}",
                data=data,
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            _LOGGER.error("TionClient: request exception while getting token:\n%s", e)
            return False

        if response.status_code == 200:
            response = response.json()
            self._authorization = f"{response['token_type']} {response['access_token']}"

            _LOGGER.info("TionClient: got new authorization token")

            for coro in self._auth_update_listeners:
                coro(
                    username=self._email,
                    password=self._password,
                    scan_interval=self._min_update_interval,
                    auth=self._authorization,
                )

            return True

        _LOGGER.error(
            "TionClient: response while getting token: status code: %s, content:\n%s",
            response.status_code,
            response.json(),
        )
        return False

    def get_location_data(self, force=False) -> bool:
        """Get locations data from Tion API."""
        if not force and (time() - self._last_update) < self._min_update_interval:
            _LOGGER.debug(
                "TionClient: location data already updated recently. Skipping request"
            )
            return self._locations is not None

        try:
            response = requests.get(
                f"{self._API_ENDPOINT}{self._LOCATION_URL}",
                headers=self.headers,
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            _LOGGER.error(
                "TionClient: request exception while getting location data:\n%s", e
            )
            return False

        if response.status_code == 200:
            self._locations = [TionLocation(location) for location in response.json()]
            self._last_update = time()
            return True

        if response.status_code == 401:
            _LOGGER.info("TionClient: need to get new authorization")
            if self._get_authorization():
                return self.get_location_data(force=True)

            _LOGGER.error("TionClient: authorization failed!")
        else:
            _LOGGER.error(
                "TionClient: response while getting location data: status code: %s, content:\n%s",
                response.status_code,
                response.json(),
            )

        return False

    def get_zone(self, guid: str, force=False) -> TionZone | None:
        """Get zone data by guid from Tion API."""
        if self.get_location_data(force=force):
            for location in self._locations:
                for zone_data in location.zones:
                    if zone_data.guid == guid:
                        return zone_data

    def get_device(self, guid: str, force=False) -> TionZoneDevice | None:
        """Get device data by guid from Tion API."""
        if self.get_location_data(force=force):
            for location in self._locations:
                for zone in location.zones:
                    for device in zone.devices:
                        if device.guid == guid:
                            return device

    def get_device_zone(self, guid: str, force=False) -> TionZone | None:
        """Get device zone data by device guid from Tion API."""
        if self.get_location_data(force=force):
            for location in self._locations:
                for zone in location.zones:
                    for device in zone.devices:
                        if device.guid == guid:
                            return zone

    def get_devices(self, force=False) -> list[TionZoneDevice]:
        """Get all devices data from Tion API."""
        if self.get_location_data(force=force):
            return [
                device
                for location in self._locations
                for zone in location.zones
                for device in zone.devices
            ]

        return []

    def send_breezer(
        self,
        guid: str,
        is_on: bool,
        t_set: int,
        speed: int,
        speed_min_set: int,
        speed_max_set: int,
        heater_enabled: bool | None = None,
        heater_mode: str | None = None,
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

        return self._send(url, data)

    def send_zone(self, guid: str, mode: str, co2: int):
        """Send new zone data to API."""
        url = f"{self._API_ENDPOINT}{self._ZONE_URL}/{guid}/mode"
        data = {
            "mode": mode,
            "co2": co2,
        }

        return self._send(url, data)

    def _send(self, url: str, data: dict[str, Any]):
        try:
            response = requests.post(
                url=url,
                json=data,
                headers=self.headers,
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            _LOGGER.error(
                "TionClient: request exception while sending new data:\n%s", e
            )
            return False

        response = response.json()
        if response["status"] != "queued":
            _LOGGER.error(
                "TionClient: parameters set %s: %s",
                response["status"],
                response["description"],
            )
            return False

        return self._wait_for_task(response["task_id"])

    def _wait_for_task(self, task_id: str, max_time: int = 5) -> bool:
        """Wait for task with defined task_id been completed."""
        DELAY = 0.5
        for _ in range(int(max_time / DELAY)):
            try:
                response = requests.get(
                    url=f"{self._API_ENDPOINT}{self._TASK_URL}/{task_id}",
                    headers=self.headers,
                    timeout=max_time,
                )
            except requests.exceptions.RequestException as e:
                _LOGGER.error(
                    "TionClient: request exception while waiting for a task:\n%s", e
                )
                return False

            if response.status_code == 200:
                if response.json()["status"] == "completed":
                    self.get_location_data(force=True)
                    return True

                sleep(DELAY)
            else:
                _LOGGER.warning(
                    "TionClient: иad response code %s while waiting for a task, content:\n%s",
                    response.status_code,
                    response.text,
                )
                return False

        _LOGGER.warning(
            "TionClient: сouldn't get completed status for %s sec while waiting for a task",
            response.text,
        )
        return False