"""Location structure: GetFullStructureLocations and room auto mode messages."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from .exceptions import TionApiError
from .model import AutoControl, Device, Location, Room
from .profiles import DeviceProfile
from .protobuf import ProtoMessage, encode_bytes, encode_guid, encode_varint

SVC_LOCATION_READER = "api.v1.scheme.location.LocationServiceReader"
METHOD_GET_STRUCTURE = "GetFullStructureLocations"
SVC_ROOM_WRITER = "api.v1.scheme.room.RoomServiceWriter"
METHOD_SET_AUTO_CONTROL = "SetAutoControlParams"
EVENT_AUTO_CONTROL_CHANGED = "AutoControlChanged"

# Creators the Tion app treats as "synced from the old cloud, not migrated yet".
LEGACY_CREATOR = UUID("00000000-0000-0000-0000-000000000001")
_CLOUD_STUB_CREATORS = frozenset(
    {LEGACY_CREATOR, UUID("00000000-0000-0000-0000-000000000005")}
)
_MAC_FIELDS = (16, 17, 18, 19)


@dataclass(frozen=True, slots=True)
class Structure:
    """The account's locations and the token that opens the NATS channel."""

    locations: tuple[Location, ...]
    wstoken: str

    def profile_ids(self) -> set[UUID]:
        """Return every profile id the devices reference."""
        return {
            device.profile_id
            for location in self.locations
            for device in location.devices
            if device.profile_id is not None
        }


def decode_structure(
    payload: bytes, profiles: Mapping[UUID, DeviceProfile]
) -> Structure:
    """Decode a GetFullStructureLocationsResponse."""
    response = ProtoMessage.parse(payload)
    if (error := response.get_message(2)) is not None:
        raise TionApiError(
            f"{METHOD_GET_STRUCTURE} failed ({error.get_int(1)}): {error.get_str(2)}"
        )
    if (success := response.get_message(1)) is None:
        raise TionApiError(f"{METHOD_GET_STRUCTURE}: unrecognized response")
    return Structure(
        locations=tuple(
            _decode_location(item, profiles) for item in success.get_messages(1)
        ),
        wstoken=success.get_str(2),
    )


def _wrapped(message: ProtoMessage, field: int) -> ProtoMessage:
    """Return a {Value = 1} wrapper message, empty if absent."""
    return message.get_message(field) or ProtoMessage.parse(b"")


def _wrapped_guid(message: ProtoMessage, field: int) -> UUID | None:
    return _wrapped(message, field).get_guid(1)


def _decode_location(
    message: ProtoMessage, profiles: Mapping[UUID, DeviceProfile]
) -> Location:
    return Location(
        id=_wrapped_guid(message, 1) or UUID(int=0),
        sid=_wrapped(message, 2).get_str(1),
        name=_wrapped(message, 3).get_str(1),
        rooms=tuple(_decode_room(item) for item in message.get_messages(9)),
        devices=tuple(
            _decode_device(item, profiles) for item in message.get_messages(10)
        ),
        needs_migration=_wrapped_guid(message, 5) == LEGACY_CREATOR,
    )


def _decode_room(message: ProtoMessage) -> Room:
    auto = message.get_message(7)
    return Room(
        id=_wrapped_guid(message, 1) or UUID(int=0),
        name=_wrapped(message, 2).get_str(1),
        auto=_decode_auto_control(auto) if auto is not None else None,
    )


def _decode_auto_control(message: ProtoMessage) -> AutoControl:
    return AutoControl(
        enabled=message.get_bool(1),
        speed_min=message.get_int(2),
        speed_max=message.get_int(3),
        co2_target=message.get_int(4),
        algorithm=message.get_int(5),
    )


def _decode_device(
    message: ProtoMessage, profiles: Mapping[UUID, DeviceProfile]
) -> Device:
    profile_id = _wrapped_guid(message, 11)
    parent = message.get_message(20)
    return Device(
        id=_wrapped(message, 1).get_str(1),
        name=_wrapped(message, 2).get_str(1),
        model=_wrapped(message, 3).get_sint64(1),
        submodel=_wrapped(message, 4).get_int(1),
        room_id=_wrapped_guid(message, 10),
        parent_id=parent.get_str(1) if parent is not None else None,
        is_online=message.get_bool(15),
        is_gateway=message.get_bool(23),
        firmware=_wrapped(message, 5).get_sint64(1),
        hardware=_wrapped(message, 6).get_sint64(1),
        macs=_macs(message),
        is_cloud_stub=_wrapped_guid(message, 7) in _CLOUD_STUB_CREATORS,
        profile_id=profile_id,
        profile=profiles.get(profile_id) if profile_id is not None else None,
    )


def _macs(message: ProtoMessage) -> tuple[str, ...]:
    """Return the device's non-zero MACs, deduplicated, as aa:bb:cc:dd:ee:ff."""
    macs: list[str] = []
    for field in _MAC_FIELDS:
        value = _wrapped(message, field).get_int(1)
        if not value:
            continue
        mac = ":".join(f"{byte:02x}" for byte in value.to_bytes(8, "big")[2:])
        if mac not in macs:
            macs.append(mac)
    return tuple(macs)


def encode_set_auto_control(room_id: UUID, auto: AutoControl) -> bytes:
    """Encode a SetAutoControlParamsRequest; the server wants every field."""
    return (
        encode_bytes(1, encode_guid(1, room_id))
        + encode_varint(2, int(auto.enabled))
        + encode_varint(3, auto.speed_min)
        + encode_varint(4, auto.speed_max)
        + encode_varint(5, auto.co2_target)
        + encode_varint(6, auto.algorithm)
    )


def check_set_auto_control(payload: bytes) -> None:
    """Raise TionApiError unless a SetAutoControlParamsResponse says Ok."""
    response = ProtoMessage.parse(payload)
    if (error := response.get_message(2)) is not None:
        raise TionApiError(
            f"{METHOD_SET_AUTO_CONTROL} failed ({error.get_int(1)}): {error.get_str(2)}"
        )
    if not response.get_bool(1):
        raise TionApiError(f"{METHOD_SET_AUTO_CONTROL}: unrecognized response")


def decode_auto_control_changed(payload: bytes) -> tuple[UUID, AutoControl | None]:
    """Decode an AutoControlChanged event into the room and its new auto mode."""
    event = ProtoMessage.parse(payload)
    room_id = _wrapped_guid(event, 2)
    if room_id is None:
        raise TionApiError(f"{EVENT_AUTO_CONTROL_CHANGED} without a room")
    if event.get_bool(6):
        return room_id, None
    params = _wrapped(event, 5)
    return room_id, AutoControl(
        enabled=event.get_bool(3),
        speed_min=params.get_int(1),
        speed_max=params.get_int(2),
        co2_target=params.get_int(3),
        algorithm=params.get_int(4),
    )
