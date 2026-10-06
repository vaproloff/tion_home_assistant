"""Semantic device views: the only place that knows datapoint codes."""

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from .datapoints import DPKind, DPValue
from .model import Device
from .profiles import DPSpec, DPType

FILTER_RESOURCE_SECONDS = 15_552_000  # 180 days, the filter life of every breezer
TARGET_TEMPERATURE_RANGE = (0.0, 30.0)

BREEZER_PRODUCTS = frozenset({"b4s_ble", "br3s_rf", "o2_rf"})
STATION_PRODUCTS = frozenset({"bs3xx_rf", "bs4xx_rf", "thco2_rf"})
# Used only when the breezer does not report fan_speed_maxavail.
_SPEED_MAX_FALLBACK = {"b4s_ble": 6, "br3s_rf": 6, "o2_rf": 4}

_CLIMATIC_HEATER_INSTALLED = 0
_CLIMATIC_FILTER_REPLACE = 2


class Flap(IntEnum):
    """Where a breezer takes air from (flap_mode)."""

    OUTSIDE = 0
    INSIDE = 1
    MIXED = 2


# A 4S takes MIXED but stays INSIDE; the O2 is assumed to match the 4S.
_FLAP_MODES = {"br3s_rf": (Flap.OUTSIDE, Flap.INSIDE, Flap.MIXED)}
_DEFAULT_FLAP_MODES = (Flap.OUTSIDE, Flap.INSIDE)


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    """Datapoint values to send to one device."""

    device_id: str
    values: tuple[DPValue, ...]


class _DeviceView:
    """Typed access to a device's datapoints through its profile."""

    _FEATURES: dict[str, str] = {}
    # Command-only names and the datapoint each one writes.
    _COMMANDS: dict[str, str] = {}
    # Polled besides the features: state flags and inputs of derived values.
    _EXTRA_QUERY: frozenset[str] = frozenset({"state_flags"})

    def __init__(self, device: Device) -> None:
        """Wrap a device that has a profile."""
        if device.profile is None:
            raise ValueError("Device has no profile")
        self.device = device
        self._profile = device.profile

    @property
    def id(self) -> str:
        """Return the device id."""
        return self.device.id

    def supports(self, name: str) -> bool:
        """Return True if the model has the datapoint behind a property or command."""
        return self._spec(self._code(name)) is not None

    def can_set(self, name: str) -> bool:
        """Return True if command() can set this property or command for the model."""
        spec = self._spec(self._code(name))
        return spec is not None and spec.writable

    def query_dp_ids(self) -> list[int]:
        """Return the datapoints to ask the device for, sorted."""
        codes = self._EXTRA_QUERY | set(self._FEATURES.values())
        return sorted(spec.dp_id for spec in self._profile.dps if spec.code in codes)

    def _code(self, name: str) -> str:
        if (code := self._FEATURES.get(name, self._COMMANDS.get(name))) is None:
            raise ValueError(f"{type(self).__name__} has no {name}")
        return code

    def _spec(self, code: str) -> DPSpec | None:
        return self._profile.by_code(code)

    def _raw(self, code: str) -> Any:
        if (spec := self._spec(code)) is None:
            return None
        value = self.device.dps.get(spec.dp_id)
        return value.value if value is not None else None

    def _number(self, code: str) -> float | None:
        spec = self._spec(code)
        if spec is None or (raw := self._raw(code)) is None:
            return None
        return raw / 10**spec.scale if spec.scale else raw

    def _switch(self, code: str) -> bool | None:
        raw = self._raw(code)
        return bool(raw) if raw is not None else None

    def _flag(self, code: str, bit: int) -> bool | None:
        raw = self._raw(code)
        return bool(raw >> bit & 1) if raw is not None else None

    def _command(self, changes: dict[str, Any]) -> DeviceCommand:
        if not changes:
            raise ValueError("No changes to send")
        values = tuple(self._encode(name, value) for name, value in changes.items())
        return DeviceCommand(self.device.id, values)

    def _encode(self, name: str, value: Any) -> DPValue:
        raise ValueError(f"{type(self).__name__} cannot set {name}")

    def _writable(self, code: str) -> DPSpec:
        spec = self._spec(code)
        if spec is None or not spec.writable:
            raise ValueError(f"{self.device.product_id} cannot set {code}")
        return spec


def _encode_value(spec: DPSpec, value: Any) -> DPValue:
    """Encode a natural value for a datapoint, checking the profile range."""
    match spec.type:
        case DPType.BOOL:
            return DPValue(spec.dp_id, DPKind.BOOL, bool(value))
        case DPType.VALUE:
            raw = round(value * 10**spec.scale)
            if (spec.minimum is not None and raw < spec.minimum) or (
                spec.maximum is not None and raw > spec.maximum
            ):
                raise ValueError(f"{spec.code}={value} is out of range")
            return DPValue(spec.dp_id, DPKind.INT, raw)
        case DPType.ENUM:
            return DPValue(spec.dp_id, DPKind.ENUM, int(value))
    raise ValueError(f"{spec.code} of type {spec.type.name} is not settable")


class Breezer(_DeviceView):
    """A Breezer 4S, 3S or O2."""

    _FEATURES = {
        "is_on": "on_off",
        "speed": "fan_speed_level",
        "target_temperature": "target_temperature",
        "temperature_outdoor": "temp_outdoor",
        "temperature_outlet": "temp_indoor",
        "heater_installed": "climatic_flags",
        "heater_enabled": "heater_on_off",
        "heater_power": "sensor_heater_power_percent",
        "heater_type": "sensor_heater_type",
        "flap": "flap_mode",
        "filter_remaining": "filter_hours",
        "filter_needs_replacement": "climatic_flags",
        "backlight": "brightness_onoff",
        "sound": "beeper_on_off",
    }
    _COMMANDS = {"filter_reset": "filter_hours"}
    _EXTRA_QUERY = frozenset({"state_flags", "fan_speed_maxavail"})

    @property
    def is_on(self) -> bool | None:
        """Return True if the breezer is on."""
        return self._switch("on_off")

    @property
    def speed(self) -> int | None:
        """Return the fan speed level."""
        return self._raw("fan_speed_level")

    @property
    def speed_max(self) -> int | None:
        """Return the highest fan speed level of this breezer."""
        if reported := self._raw("fan_speed_maxavail"):
            return reported
        return _SPEED_MAX_FALLBACK.get(self.device.product_id)

    @property
    def target_temperature(self) -> float | None:
        """Return the target outlet temperature, °C."""
        return self._number("target_temperature")

    @property
    def target_temperature_range(self) -> tuple[float, float]:
        """Return the settable target temperature range, °C."""
        return TARGET_TEMPERATURE_RANGE

    @property
    def temperature_outdoor(self) -> float | None:
        """Return the temperature of the intake (outdoor) air, °C."""
        return self._number("temp_outdoor")

    @property
    def temperature_outlet(self) -> float | None:
        """Return the temperature of the air leaving the breezer, °C."""
        return self._number("temp_indoor")

    @property
    def heater_installed(self) -> bool | None:
        """Return True if the breezer has a heater."""
        return self._flag("climatic_flags", _CLIMATIC_HEATER_INSTALLED)

    @property
    def heater_enabled(self) -> bool | None:
        """Return True if heating is allowed."""
        spec = self._spec("heater_on_off")
        raw = self._raw("heater_on_off")
        if spec is None or raw is None:
            return None
        # 4S models it as a level where 0 allows heating; 3S/O2 as a bool.
        return raw == 0 if spec.type is DPType.VALUE else bool(raw)

    @property
    def heater_power(self) -> int | None:
        """Return the heater power, %."""
        return self._raw("sensor_heater_power_percent")

    @property
    def heater_type(self) -> int | None:
        """Return the heater type code."""
        return self._raw("sensor_heater_type")

    @property
    def flap(self) -> Flap | None:
        """Return where the breezer takes air from."""
        raw = self._raw("flap_mode")
        return Flap(raw) if raw in Flap else None

    @property
    def flap_modes(self) -> tuple[Flap, ...]:
        """Return the flap modes the breezer can be set to."""
        if not self.can_set("flap"):
            return ()
        return _FLAP_MODES.get(self.device.product_id, _DEFAULT_FLAP_MODES)

    @property
    def filter_remaining(self) -> int | None:
        """Return the remaining filter life, seconds."""
        return self._raw("filter_hours")

    @property
    def filter_needs_replacement(self) -> bool | None:
        """Return True if the filter must be replaced."""
        return self._flag("climatic_flags", _CLIMATIC_FILTER_REPLACE)

    @property
    def backlight(self) -> bool | None:
        """Return True if the backlight is on."""
        return self._switch("brightness_onoff")

    @property
    def sound(self) -> bool | None:
        """Return True if the sound signal is on."""
        return self._switch("beeper_on_off")

    def command(self, **changes: Any) -> DeviceCommand:
        """Build a command from property values, plus filter_reset=True."""
        return self._command(changes)

    def _encode(self, name: str, value: Any) -> DPValue:
        match name:
            case "speed":
                speed_max = self.speed_max
                if speed_max is not None and not 0 <= value <= speed_max:
                    raise ValueError(f"speed={value} is out of 0..{speed_max}")
                return _encode_value(self._writable("fan_speed_level"), value)
            case "target_temperature":
                low, high = TARGET_TEMPERATURE_RANGE
                if not low <= value <= high:
                    raise ValueError(f"target_temperature={value} is out of range")
                return _encode_value(self._writable("target_temperature"), value)
            case "heater_enabled":
                spec = self._writable("heater_on_off")
                if spec.type is DPType.VALUE:
                    return _encode_value(spec, 0 if value else 1)
                return _encode_value(spec, value)
            case "filter_reset":
                if value is not True:
                    raise ValueError("filter_reset only accepts True")
                spec = self._writable("filter_hours")
                return _encode_value(spec, FILTER_RESOURCE_SECONDS)
            case "flap":
                if value not in self.flap_modes:
                    raise ValueError(
                        f"{self.device.product_id} cannot set flap={value}"
                    )
                return _encode_value(self._writable("flap_mode"), int(value))
            case "is_on" | "backlight" | "sound":
                code = self._FEATURES[name]
                return _encode_value(self._writable(code), int(value))
        return super()._encode(name, value)


class Station(_DeviceView):
    """A MagicAir base station (BS310, BS410) or a Module CO2+."""

    _FEATURES = {
        "co2": "co2ppm",
        "temperature": "temperature_external",
        "humidity": "humidityexternal",
        "pm25": "pm2p5",
        "backlight": "brightness_onoff",
    }

    @property
    def co2(self) -> int | None:
        """Return the CO2 level, ppm."""
        return self._raw("co2ppm")

    @property
    def temperature(self) -> float | None:
        """Return the room temperature, °C."""
        return self._number("temperature_external")

    @property
    def humidity(self) -> float | None:
        """Return the room humidity, %."""
        return self._number("humidityexternal")

    @property
    def pm25(self) -> int | None:
        """Return PM2.5, µg/m³ (BS410 only)."""
        return self._raw("pm2p5")

    @property
    def backlight(self) -> bool | None:
        """Return True if the station's light is on."""
        return self._switch("brightness_onoff")

    def command(self, **changes: Any) -> DeviceCommand:
        """Build a command; a station only has its light to set."""
        return self._command(changes)

    def _encode(self, name: str, value: Any) -> DPValue:
        if name == "backlight":
            return _encode_value(self._writable("brightness_onoff"), int(value))
        return super()._encode(name, value)


def view(device: Device) -> Breezer | Station | None:
    """Return the semantic view of a supported device, or None."""
    if device.product_id in BREEZER_PRODUCTS:
        return Breezer(device)
    if device.product_id in STATION_PRODUCTS:
        return Station(device)
    return None
