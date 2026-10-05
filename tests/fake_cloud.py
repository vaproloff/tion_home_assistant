"""A TionCloud stand-in over a real account snapshot, for the HA layer tests."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID

from custom_components.tion.api import (
    AutoControl,
    Device,
    DeviceCommand,
    Location,
    Room,
    TionAccount,
    TionAuthError,
    TionTokens,
)
from custom_components.tion.api.datapoints import DPKind, DPRaw, DPValue
from custom_components.tion.api.profiles import DeviceProfile, decode_profiles

from .api.payloads import (  # noqa: TID251
    PROFILE_3S,
    PROFILE_4S,
    PROFILE_BS310,
    PROFILE_BS410,
    PROFILE_CLEVER,
    PROFILE_CO2,
    PROFILE_O2,
)

PROFILES_FILE = Path(__file__).parent / "api" / "fixtures" / "device_profiles.bin"

LOCATION_ID = UUID(int=1)
BEDROOM_ID = UUID(int=0x101)
LIVING_ROOM_ID = UUID(int=0x102)

BREEZER_4S = "B4S0000001"
BREEZER_3S = "B3S0000001"
BREEZER_O2 = "BO20000001"
MAGICAIR = "BS40000001"
MAGICAIR_310 = "BS30000001"
MODULE_CO2 = "MCO0000001"
CLEVER = "CLV0000001"

BEDROOM_AUTO = AutoControl(enabled=False, speed_min=1, speed_max=4, co2_target=800)


@cache
def profiles() -> dict[UUID, DeviceProfile]:
    """Return the recorded profile catalog."""
    return decode_profiles(PROFILES_FILE.read_bytes())


def dps(profile_id: UUID, **raw: DPRaw) -> dict[int, DPValue]:
    """Return datapoint values by profile code, in wire units."""
    profile = profiles()[profile_id]
    values: dict[int, DPValue] = {}
    for code, value in raw.items():
        spec = profile.by_code(code)
        assert spec is not None, code
        # DPType and DPKind share their numbering.
        values[spec.dp_id] = DPValue(spec.dp_id, DPKind(int(spec.type)), value)
    return values


def device(
    device_id: str,
    profile_id: UUID,
    name: str,
    *,
    room_id: UUID | None = BEDROOM_ID,
    parent_id: str | None = None,
    is_gateway: bool = False,
    is_online: bool = True,
    values: Mapping[int, DPValue] | None = None,
) -> Device:
    """Return a device of the account with a real profile."""
    return Device(
        id=device_id,
        name=name,
        model=0,
        submodel=0,
        room_id=room_id,
        parent_id=parent_id,
        is_online=is_online,
        is_gateway=is_gateway,
        firmware=1163,
        hardware=1,
        macs=(":".join(f"{byte:02x}" for byte in device_id.encode()[:6]),),
        is_cloud_stub=False,
        profile_id=profile_id,
        profile=profiles()[profile_id],
        dps=dict(values or {}),
    )


def default_account() -> TionAccount:
    """Return an account with every supported model and one unsupported."""
    devices = (
        device(
            MAGICAIR,
            PROFILE_BS410,
            "MagicAir",
            is_gateway=True,
            values=dps(
                PROFILE_BS410,
                co2ppm=650,
                temperature_external=224,
                humidityexternal=410,
                pm2p5=12,
                brightness_onoff=1,
            ),
        ),
        device(
            BREEZER_4S,
            PROFILE_4S,
            "Breezer 4S",
            parent_id=MAGICAIR,
            values=dps(
                PROFILE_4S,
                on_off=True,
                fan_speed_level=3,
                fan_speed_maxavail=6,
                target_temperature=150,
                temp_outdoor=-52,
                temp_indoor=185,
                climatic_flags=0b011,
                heater_on_off=0,
                sensor_heater_power_percent=40,
                sensor_heater_type=1,
                flap_mode=0,
                filter_hours=2_466_193,
                brightness_onoff=1,
                beeper_on_off=0,
            ),
        ),
        device(
            BREEZER_3S,
            PROFILE_3S,
            "Breezer 3S",
            room_id=LIVING_ROOM_ID,
            values=dps(
                PROFILE_3S,
                on_off=False,
                fan_speed_level=2,
                target_temperature=180,
                temp_outdoor=31,
                temp_indoor=205,
                climatic_flags=0b001,
                heater_on_off=False,
                flap_mode=2,
                filter_hours=0,
            ),
        ),
        device(
            BREEZER_O2,
            PROFILE_O2,
            "Breezer O2",
            room_id=None,
            values=dps(
                PROFILE_O2,
                on_off=True,
                fan_speed_level=1,
                temp_outdoor=-100,
                temp_indoor=160,
                climatic_flags=0b100,
                heater_on_off=False,
                flap_mode=1,
                filter_hours=86_399,
            ),
        ),
        device(
            MAGICAIR_310,
            PROFILE_BS310,
            "MagicAir 310",
            room_id=LIVING_ROOM_ID,
            values=dps(
                PROFILE_BS310,
                co2ppm=900,
                temperature_external=231,
                humidityexternal=385,
                brightness_onoff=0,
            ),
        ),
        device(
            MODULE_CO2,
            PROFILE_CO2,
            "Module CO2+",
            values=dps(
                PROFILE_CO2,
                co2ppm=640,
                temperature_external=225,
                humidityexternal=400,
                brightness_onoff=1,
            ),
        ),
        device(CLEVER, PROFILE_CLEVER, "Clever"),
    )
    rooms = (
        Room(BEDROOM_ID, "Bedroom", BEDROOM_AUTO),
        Room(LIVING_ROOM_ID, "Living room", None),
    )
    home = Location(LOCATION_ID, "LOC0000001", "Home", rooms, devices, False)
    return TionAccount((home,), connected=True)


def replace_device(account: TionAccount, device_id: str, **changes: Any) -> TionAccount:
    """Return the account with one device's fields changed."""
    return _map_location(
        account,
        lambda location: replace(
            location,
            devices=tuple(
                replace(item, **changes) if item.id == device_id else item
                for item in location.devices
            ),
        ),
    )


def set_values(
    account: TionAccount, device_id: str, values: Mapping[int, DPValue]
) -> TionAccount:
    """Return the account with datapoint values of one device merged."""
    current = account.device(device_id)
    assert current is not None
    return replace_device(account, device_id, dps={**current.dps, **values})


def set_room_auto(
    account: TionAccount, room_id: UUID, auto: AutoControl | None
) -> TionAccount:
    """Return the account with a room's auto mode replaced."""
    return _map_location(
        account,
        lambda location: replace(
            location,
            rooms=tuple(
                replace(room, auto=auto) if room.id == room_id else room
                for room in location.rooms
            ),
        ),
    )


def _map_location(
    account: TionAccount, update: Callable[[Location], Location]
) -> TionAccount:
    return replace(account, locations=tuple(update(item) for item in account.locations))


class FakeAuth:
    """The session part the integration uses: token updates."""

    def __init__(self) -> None:
        """Start without listeners."""
        self._listeners: list[Callable[[TionTokens], None]] = []

    def add_update_listener(
        self, listener: Callable[[TionTokens], None]
    ) -> Callable[[], None]:
        """Register a token listener; returns unsubscribe."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def renew(self, tokens: TionTokens) -> None:
        """Hand new tokens to the listeners, as a renewal does."""
        for listener in list(self._listeners):
            listener(tokens)


class FakeTionCloud:
    """Holds an account snapshot, records commands and applies them."""

    def __init__(self, account: TionAccount | None = None) -> None:
        """Start with the default account, connected."""
        self.account = account or default_account()
        self.auth_error: TionAuthError | None = None
        self.start_error: Exception | None = None
        self.refresh_error: Exception | None = None
        self.command_error: Exception | None = None
        self.calls: list[tuple[str, Any]] = []
        self.refreshes = 0
        self.stopped = False
        self.listeners: list[Callable[[], None]] = []

    @property
    def connected(self) -> bool:
        """Return True while the live channel is up."""
        return self.account.connected

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a snapshot listener; returns unsubscribe."""
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    async def async_start(self) -> None:
        """Fail if told to."""
        if self.start_error is not None:
            raise self.start_error

    async def async_refresh(self) -> None:
        """Count the refresh and notify, or fail if told to."""
        self.refreshes += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        self.push(self.account)

    async def async_stop(self) -> None:
        """Disconnect, as the real cloud does."""
        self.stopped = True
        self.push(replace(self.account, connected=False))

    async def async_command(self, command: DeviceCommand) -> None:
        """Record the command and apply its values."""
        self.calls.append(("command", command))
        if self.command_error is not None:
            raise self.command_error
        self.push(
            set_values(
                self.account,
                command.device_id,
                {value.dp_id: value for value in command.values},
            )
        )

    async def async_set_auto_control(self, room_id: UUID, **changes: Any) -> None:
        """Record the change and apply it to the room's auto mode."""
        self.calls.append(("auto_control", (room_id, changes)))
        if self.command_error is not None:
            raise self.command_error
        room = self.account.room(room_id)
        assert room is not None and room.auto is not None
        self.push(set_room_auto(self.account, room_id, replace(room.auto, **changes)))

    def push(self, account: TionAccount) -> None:
        """Replace the snapshot and notify, as a push does."""
        self.account = account
        for listener in list(self.listeners):
            listener()
