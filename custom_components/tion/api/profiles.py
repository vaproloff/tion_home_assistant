"""Device profiles: what each datapoint of a Tion model means."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum
from uuid import UUID

from .protobuf import ProtoMessage

SVC_PROFILES = "api.v1.device.profile.DeviceProfileLookupService"
METHOD_GET_PROFILES = "GetDeviceProfiles"


class DPType(IntEnum):
    """Datapoint type (iot.device.schema.DPType)."""

    BOOL = 1
    VALUE = 2
    ENUM = 3
    STRING = 4
    RAW = 5
    FLAGS = 6
    FLOAT = 7


class DPAccess(IntEnum):
    """Datapoint access mode (iot.device.schema.DPAccessMode)."""

    READ_WRITE = 1
    READ_ONLY = 2
    WRITE_ONLY = 3


@dataclass(frozen=True, slots=True)
class DPSpec:
    """One datapoint of a profile; scale is the number of decimal places."""

    dp_id: int
    code: str
    type: DPType
    access: DPAccess
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    scale: int = 0
    unit: str = ""
    labels: Mapping[int, str] = field(default_factory=dict)

    @property
    def writable(self) -> bool:
        """Return True if the datapoint accepts commands."""
        return self.access != DPAccess.READ_ONLY


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """A device model as described by the cloud's profile catalog."""

    profile_id: UUID
    product_id: str
    name: str
    model: int
    submodels: tuple[int, ...]
    is_gateway: bool
    dps: tuple[DPSpec, ...]

    def spec(self, dp_id: int) -> DPSpec | None:
        """Return the datapoint with this id, if the model has it."""
        return next((spec for spec in self.dps if spec.dp_id == dp_id), None)

    def by_code(self, code: str) -> DPSpec | None:
        """Return the datapoint with this code, if the model has it."""
        return next((spec for spec in self.dps if spec.code == code), None)


def decode_profiles(payload: bytes) -> dict[UUID, DeviceProfile]:
    """Decode a GetDeviceProfilesResponse into profiles by id."""
    profiles: dict[UUID, DeviceProfile] = {}
    for entry in ProtoMessage.parse(payload).get_messages(1):
        wrapper = entry.get_message(1)
        profile_id = wrapper.get_guid(1) if wrapper is not None else None
        if profile_id is None:
            continue
        profiles[profile_id] = decode_profile(profile_id, entry.get_bytes(2))
    return profiles


def decode_profile(profile_id: UUID, data: bytes) -> DeviceProfile:
    """Decode a serialized DeviceProfileSource."""
    source = ProtoMessage.parse(data)
    return DeviceProfile(
        profile_id=profile_id,
        product_id=source.get_str(1),
        name=source.get_str(2),
        model=source.get_int(7),
        submodels=tuple(source.get_packed(8)),
        is_gateway=source.get_bool(19),
        dps=tuple(
            spec
            for item in source.get_messages(13)
            if (spec := _decode_spec(item)) is not None
        ),
    )


def _decode_spec(message: ProtoMessage) -> DPSpec | None:
    """Decode a DPSchema; None for a type or access mode this code predates."""
    try:
        dp_type = DPType(message.get_int(4))
        access = DPAccess(message.get_int(5))
    except ValueError:
        return None
    spec = DPSpec(
        dp_id=message.get_int(1),
        code=message.get_str(2),
        type=dp_type,
        access=access,
    )
    if (prop := message.get_message(8)) is not None:
        return replace(
            spec,
            minimum=prop.get_sint64(1),
            maximum=prop.get_sint64(2),
            step=prop.get_sint64(3),
            scale=prop.get_sint64(4),
            unit=prop.get_str(5),
        )
    if (prop := message.get_message(13)) is not None:
        return replace(
            spec,
            minimum=prop.get_float(1),
            maximum=prop.get_float(2),
            step=prop.get_float(3),
            scale=prop.get_int(4),
            unit=prop.get_str(5),
        )
    if (prop := message.get_message(9)) is not None:
        return replace(spec, labels=prop.get_map(1))
    if (prop := message.get_message(12)) is not None:
        return replace(spec, labels=prop.get_map(1))
    if (prop := message.get_message(14)) is not None:
        return replace(spec, labels={1: prop.get_str(1), 0: prop.get_str(2)})
    return spec
