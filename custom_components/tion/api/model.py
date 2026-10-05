"""Immutable snapshot of a Tion account: locations, rooms, devices and state."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from uuid import UUID

from .datapoints import DPValue
from .profiles import DeviceProfile


class AutoControlAlgorithm(IntEnum):
    """How a room combines several CO2 sensors (domain.enums.AutoControlCo2Algorithm)."""

    AVERAGE = 1
    MAXIMUM = 2


@dataclass(frozen=True, slots=True)
class AutoControl:
    """A room's CO2 auto mode; the cloud drives the room's breezers within it."""

    enabled: bool
    speed_min: int
    speed_max: int
    co2_target: int
    algorithm: int = AutoControlAlgorithm.AVERAGE


@dataclass(frozen=True, slots=True)
class Room:
    """A room of a location."""

    id: UUID
    name: str
    auto: AutoControl | None

    @property
    def configured_auto(self) -> AutoControl | None:
        """Return the auto mode if it is set up (speed_max 0 is an empty message)."""
        return self.auto if self.auto is not None and self.auto.speed_max > 0 else None


@dataclass(frozen=True, slots=True)
class Device:
    """A device with its latest datapoint values."""

    id: str
    name: str
    model: int
    submodel: int
    room_id: UUID | None
    parent_id: str | None
    is_online: bool
    is_gateway: bool
    firmware: int
    hardware: int
    macs: tuple[str, ...]
    is_cloud_stub: bool
    profile_id: UUID | None
    profile: DeviceProfile | None
    dps: Mapping[int, DPValue] = field(default_factory=dict)

    @property
    def product_id(self) -> str:
        """Return the profile's product id, or an empty string without one."""
        return self.profile.product_id if self.profile is not None else ""


@dataclass(frozen=True, slots=True)
class Location:
    """A location (home) with its rooms and devices."""

    id: UUID
    sid: str
    name: str
    rooms: tuple[Room, ...]
    devices: tuple[Device, ...]
    needs_migration: bool


@dataclass(frozen=True, slots=True)
class TionAccount:
    """Everything the account can see, as of one moment."""

    locations: tuple[Location, ...] = ()
    connected: bool = False

    def devices(self) -> Iterator[Device]:
        """Iterate over every device of every location."""
        for location in self.locations:
            yield from location.devices

    def device(self, device_id: str) -> Device | None:
        """Return the device with this id."""
        return next((d for d in self.devices() if d.id == device_id), None)

    def room(self, room_id: UUID) -> Room | None:
        """Return the room with this id."""
        rooms = (room for location in self.locations for room in location.rooms)
        return next((room for room in rooms if room.id == room_id), None)

    def room_of(self, device: Device) -> Room | None:
        """Return the room the device is in."""
        return self.room(device.room_id) if device.room_id is not None else None

    def location_of(self, device: Device) -> Location | None:
        """Return the location the device belongs to."""
        return next(
            (
                location
                for location in self.locations
                if any(d.id == device.id for d in location.devices)
            ),
            None,
        )
