"""The Tion API interaction module."""

import logging
from os import path
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
    _CLIENT_ID = "cd594955-f5ba-4c20-9583-5990bb29f4ef"
    _CLIENT_SECRET = "syRxSrT77P"

    def __init__(
        self,
        email: str,
        password: str,
        auth_fname="tion_auth",
        min_update_interval_sec=10,
    ) -> None:
        """Tion API client initialization."""
        self._email = email
        self._password = password
        self._auth_fname = auth_fname
        self._min_update_interval = min_update_interval_sec
        if self._auth_fname and path.exists(self._auth_fname):
            with open(self._auth_fname, encoding="utf-8") as file:
                self.authorization = file.read()
        else:
            self.authorization = None
            self._get_authorization()
        self._last_update = 0
        self._locations: list[TionLocation] = []
        self.get_location_data()

    @property
    def headers(self):
        """Return headers for API request."""
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "ru-RU",
            "Authorization": self.authorization,
            "Connection": "Keep-Alive",
            "Content-Type": "application/json",
            "Host": "api2.magicair.tion.ru",
            "Origin": "https://magicair.tion.ru",
            "Referer": "https://magicair.tion.ru/dashboard/overview",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2486.0 Safari/537.36 Edge/13.10586",
        }

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
            _LOGGER.error("Request exception while getting token:\n%s", e)
            return False

        if response.status_code == 200:
            response = response.json()
            self.authorization = f"{response['token_type']} {response['access_token']}"

            if self._auth_fname:
                try:
                    with open(self._auth_fname, "w", encoding="utf-8") as file:
                        try:
                            file.write(self.authorization)
                        except OSError as e:
                            _LOGGER.error(
                                "Unable to write auth data to %s: %s",
                                self._auth_fname,
                                e,
                            )
                except (FileNotFoundError, PermissionError, OSError) as e:
                    _LOGGER.error("Error opening file %s: %s", self._auth_fname, e)

            _LOGGER.info("Got new authorization token")
            return True

        _LOGGER.error(
            "Response while getting token: status code: %s, content:\n%s",
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
            _LOGGER.error("Request exception while getting location data:\n%s", e)
            return False

        if response.status_code == 200:
            self._locations = [TionLocation(location) for location in response.json()]
            self._last_update = time()
            return True

        if response.status_code == 401:
            _LOGGER.info("Need to get new authorization")
            if self._get_authorization():
                return self.get_location_data(force=True)

            _LOGGER.error("Authorization failed!")
        else:
            _LOGGER.error(
                "Response while getting location data: status code: %s, content:\n%s",
                response.status_code,
                response.json(),
            )

        return False

    def get_zone(self, guid: str | None = None):
        """Get Zone by guid from Tion API."""
        return Zone(self.get_zone_data(guid), self)

    def get_zone_data(self, guid: str, force=False) -> TionZone | None:
        """Get zone data by guid from Tion API."""
        if self.get_location_data(force=force):
            for location in self._locations:
                for zone_data in location.zones:
                    if zone_data.guid == guid:
                        return zone_data

    def get_devices(self, guid: str | None = None, type: str | None = None) -> list:
        """Get all devices from Tion API."""
        devices, zones = self.get_devices_data(guid, type)
        result = []
        for device_data, zone_data in zip(devices, zones, strict=True):
            if "co2" in device_data.type:
                result.append(MagicAir(device_data, Zone(zone_data, self), self))
            elif "breezer" in device_data.type or "O2" in device_data.type:
                result.append(Breezer(device_data, Zone(zone_data, self), self))
            else:
                _LOGGER.warning(
                    "Unsupported device type: %s",
                    device_data.type,
                )
        return result

    def get_devices_data(
        self,
        guid: str | None = None,
        type: str | None = None,
        force=False,
    ) -> tuple[list[TionZoneDevice], list[TionZone]]:
        """Get all devices data from Tion API."""
        devices: list[TionZoneDevice] = []
        zones: list[TionZone] = []
        if self.get_location_data(force=force):
            for location in self._locations:
                for zone in location.zones:
                    for device in zone.devices:
                        if any(
                            [
                                not guid and not type,
                                guid and device.guid == guid,
                                type and type.lower() in device.type.lower(),
                            ]
                        ):
                            devices.append(device)
                            zones.append(zone)
        return devices, zones

    def wait_for_task(self, task_id: str, max_time: int = 5) -> bool:
        """Wait for task with defined task_id been completed."""
        url = "https://api2.magicair.tion.ru/task/" + task_id
        DELAY = 0.5
        for _ in range(int(max_time / DELAY)):
            try:
                response = requests.get(url, headers=self.headers, timeout=max_time)
            except requests.exceptions.RequestException as e:
                _LOGGER.error("Exception in wait_for_task:\n%s", e)
                return False
            if response.status_code == 200:
                if response.json()["status"] == "completed":
                    self.get_location_data(force=True)
                    return True

                sleep(DELAY)
            else:
                _LOGGER.warning(
                    "Bad response code %s in wait_for_task, content:\n%s",
                    response.status_code,
                    response.text,
                )
                return False
        _LOGGER.warning(
            "Couldn't get completed status for %s sec in wait_for_task", response.text
        )
        return False


class Zone:
    """Tion Zone class."""

    def __init__(self, zone_data: TionZone, api: TionClient) -> None:
        """MagicAir station initialization."""
        self._api = api
        self._guid = zone_data.guid
        self._name = zone_data.name

        self._mode = None
        self._target_co2 = None

        self.load(zone_data)

    @property
    def guid(self) -> str:
        """Return zone guid."""
        return self._guid

    @property
    def name(self) -> str:
        """Return zone name."""
        return self._name

    @property
    def mode(self) -> str:
        """Return zone mode."""
        return self._mode

    @mode.setter
    def mode(self, new_mode: str) -> str:
        self._mode = new_mode

    @property
    def target_co2(self) -> str:
        """Return zone target CO2 level."""
        return self._target_co2

    @target_co2.setter
    def target_co2(self, target_co2: float) -> str:
        self._target_co2 = target_co2

    @property
    def valid(self):
        """Return if zone guid is not None."""
        return self._guid is not None

    def load(self, zone_data: TionZone | None = None, force=False) -> bool:
        """Update zone data from API."""
        if not zone_data:
            zone_data = self._api.get_zone_data(guid=self.guid, force=force)

        if zone_data:
            self._guid = zone_data.guid
            self._name = zone_data.name
            self._mode = zone_data.mode.current
            self._target_co2 = zone_data.mode.auto_set.co2

        return self.valid

    def send(self) -> bool:
        """Send new zone data to API."""
        if not self.valid:
            return False

        data = {
            "mode": self._mode if self._mode in ("auto", "manual") else "manual",
            "co2": int(self._target_co2) if self._target_co2 is not None else 900,
        }

        url = f"https://api2.magicair.tion.ru/zone/{self.guid}/mode"
        try:
            response = requests.post(
                url, json=data, headers=self._api.headers, timeout=10
            )
        except requests.exceptions.RequestException as e:
            _LOGGER.error("Exception while sending new zone data!:\n%s", e)
            return False

        response = response.json()
        status = response["status"]
        if status != "queued":
            _LOGGER.info("TionApi auto set %s: %s", status, response["description"])
            return False

        return self._api.wait_for_task(response["task_id"])


class MagicAir:
    """MagicAir station device class."""

    def __init__(
        self, device_data: TionZoneDevice, zone: Zone, api: TionClient
    ) -> None:
        """MagicAir station initialization."""
        self._api = api
        self._zone = zone
        self._guid = device_data.guid
        self._name = device_data.name
        self._type = device_data.type
        self._mac = device_data.mac
        self._firmware = device_data.firmware
        self._hardware = device_data.hardware

        self._co2 = None
        self._temperature = None
        self._humidity = None
        self._pm25 = None
        self._pm10 = None
        self._pm1 = None
        self._backlight = None

        self.load(device_data)

    @property
    def zone(self) -> Zone:
        """Return MagicAir Zone."""
        return self._zone

    @property
    def guid(self):
        """Return MagicAir device guid."""
        return self._guid

    @property
    def name(self):
        """Return MagicAir device name."""
        return self._name

    @property
    def type(self):
        """Return MagicAir device type."""
        return self._type

    @property
    def mac(self) -> str:
        """Return MagicAir device mac address."""
        return self._mac

    @property
    def firmware(self) -> str:
        """Return MagicAir device firmware version."""
        return self._firmware

    @property
    def hardware(self) -> str:
        """Return MagicAir device hardware version."""
        return self._hardware

    @property
    def co2(self):
        """Return current CO2 level."""
        return self._co2

    @property
    def temperature(self):
        """Return current temperature."""
        return self._temperature

    @property
    def humidity(self):
        """Return current humidity."""
        return self._humidity

    @property
    def valid(self):
        """Return if MagicAir device guid is not None."""
        return self._guid is not None

    def load(self, device_data: TionZoneDevice | None = None, force=False):
        """Update MagicAir data from API."""
        if not device_data:
            devices, _ = self._api.get_devices_data(guid=self._guid, force=force)
            if devices:
                device_data = devices[0]

        if device_data:
            data: TionZoneDeviceData = device_data.data
            self._guid = device_data.guid
            self._name = device_data.name
            self._co2 = data.co2
            self._temperature = data.temperature
            self._humidity = data.humidity
            self._pm25 = data.pm25
            self._pm10 = data.pm10
            self._pm1 = data.pm1
            self._backlight = data.backlight

        return self.valid


class Breezer:
    """Tion Breezer device class."""

    def __init__(
        self, device_data: TionZoneDevice, zone: Zone, api: TionClient
    ) -> None:
        """Tion Breezer initialization."""
        self._api = api
        self._zone = zone
        self._guid = device_data.guid
        self._name = device_data.name
        self._type = device_data.type
        self._mac = device_data.mac
        self._firmware = device_data.firmware
        self._hardware = device_data.hardware
        self._max_speed = device_data.max_speed
        self._speed_limit = device_data.data.speed_limit
        self._t_max = device_data.t_max
        self._t_min = device_data.t_min
        self._heater_type = device_data.data.heater_type
        self._heater_installed = (
            True if self._heater_type is not None else device_data.data.heater_installed
        )

        self._data_valid = None
        self._is_on = None
        self._t_in = None
        self._t_out = None
        self._t_set = None
        self._heater_enabled = None
        self._heater_mode = None
        self._heater_power = None
        self._speed = None
        self._speed_min_set = None
        self._speed_max_set = None
        self._gate = None
        self._backlight = None
        self._sound_is_on = None
        self._filter_need_replace = None

        self.load(device_data)

    @property
    def zone(self) -> Zone:
        """Return breezer Zone."""
        return self._zone

    @property
    def guid(self) -> str:
        """Return breezer device guid."""
        return self._guid

    @property
    def name(self) -> str:
        """Return breezer device name."""
        return self._name

    @property
    def type(self) -> str:
        """Return breezer device type."""
        return self._type

    @property
    def mac(self) -> str:
        """Return breezer mac address."""
        return self._mac

    @property
    def firmware(self) -> str:
        """Return breezer device firmware version."""
        return self._firmware

    @property
    def hardware(self) -> str:
        """Return breezer device hardware version."""
        return self._hardware

    @property
    def max_speed(self) -> int:
        """Return breezer max fan speed."""
        return (
            int(self._speed_limit) if self._speed_limit is not None else self._max_speed
        )

    @property
    def t_min(self) -> float:
        """Return breezer min target temperature."""
        return self._t_min

    @property
    def t_max(self) -> float:
        """Return breezer max target temperature."""
        return self._t_max

    @property
    def heater_type(self) -> str | None:
        """Return breezer heater type."""
        return self._heater_type

    @property
    def heater_installed(self) -> bool | None:
        """Return boolean is breezer heater installed."""
        return self._heater_installed

    @property
    def heater_power(self) -> int | None:
        """Return breezer current heater power."""
        return self._heater_power

    @property
    def is_on(self) -> bool:
        """Return if breezer working."""
        return self._is_on

    @is_on.setter
    def is_on(self, new_status: bool) -> None:
        self._is_on = new_status

    @property
    def t_in(self) -> float:
        """Return inside air flow temperature."""
        return self._t_in

    @property
    def t_out(self) -> float:
        """Return outside air flow temperature."""
        return self._t_out

    @property
    def t_set(self) -> float:
        """Return breezer air flow target temperature."""
        return self._t_set

    @t_set.setter
    def t_set(self, target_temp: float) -> None:
        self._t_set = target_temp

    @property
    def heater_enabled(self) -> bool:
        """Return if breezer heater working."""
        return (
            self._heater_enabled
            if self._heater_enabled is not None
            else self._heater_mode == "heat"
        )

    @heater_enabled.setter
    def heater_enabled(self, is_enabled: bool) -> None:
        if self._heater_enabled is not None:
            self._heater_enabled = is_enabled
        else:
            self._heater_mode = "heat" if is_enabled else "maintenance"

    @property
    def speed(self) -> float:
        """Return breezer current air flow speed."""
        return self._speed

    @speed.setter
    def speed(self, new_speed: int) -> None:
        self._speed = new_speed

    @property
    def speed_min_set(self):
        """Return breezer air flow min speed."""
        return self._speed_min_set

    @speed_min_set.setter
    def speed_min_set(self, new_min_speed: int) -> None:
        self._speed_min_set = new_min_speed

    @property
    def speed_max_set(self):
        """Return breezer air flow max speed."""
        return self._speed_max_set

    @speed_max_set.setter
    def speed_max_set(self, new_max_speed: int) -> None:
        self._speed_max_set = new_max_speed

    @property
    def gate(self) -> int:
        """Return breezer current air flow gate.

        Breezer 4S: 0 - inside, 1 - outside
        Other: 0 - inside, 1 - combined, 2 - outside.
        """
        return self._gate

    @gate.setter
    def gate(self, new_gate: int) -> None:
        self._gate = new_gate

    @property
    def filter_need_replace(self) -> bool:
        """Return if breezer air filters need to be replaced."""
        return self._filter_need_replace

    @property
    def valid(self) -> bool:
        """Return if breezer guid is not None and device data is valid."""
        return self.guid is not None and self._data_valid

    def load(self, device_data: TionZoneDevice | None = None, force=False):
        """Update breezer data from API."""
        if not device_data:
            devices, _ = self._api.get_devices_data(guid=self._guid, force=force)
            if devices:
                device_data = devices[0]

        if device_data:
            data: TionZoneDeviceData = device_data.data
            self._name = device_data.name
            self._guid = device_data.guid
            self._data_valid = data.data_valid
            self._is_on = data.is_on
            self._heater_enabled = data.heater_enabled
            self._heater_mode = data.heater_mode
            self._heater_power = data.heater_power
            self._t_set = data.t_set
            self._speed = data.speed
            self._speed_min_set = data.speed_min_set
            self._speed_max_set = data.speed_max_set
            self.gate = data.gate
            self._t_in = data.t_in
            self._t_out = data.t_out
            self._backlight = data.backlight
            self._sound_is_on = data.sound_is_on
            self._filter_need_replace = data.filter_need_replace

        return self.valid

    def send(self, extra_data: dict | None = None) -> bool:
        """Send new breezer data to API."""
        if not self.valid:
            return False

        data = {
            "is_on": self._is_on,
            "heater_enabled": self._heater_enabled,
            "heater_mode": self._heater_mode,
            "t_set": self.t_set,
            "speed": int(self.speed),
            "speed_min_set": self.speed_min_set,
            "speed_max_set": self.speed_max_set,
            "gate": self._gate,
        }

        if extra_data is not None:
            data.update(extra_data)

        url = f"https://api2.magicair.tion.ru/device/{self._guid}/mode"
        try:
            response = requests.post(
                url, json=data, headers=self._api.headers, timeout=10
            )
        except requests.exceptions.RequestException as e:
            _LOGGER.error("Exception while sending new breezer data!:\n%s", e)
            return False
        response = response.json()
        status = response["status"]
        if status != "queued":
            _LOGGER.error(
                "TionApi parameters set %s: %s", status, response["description"]
            )
            return False

        return self._api.wait_for_task(response["task_id"])
