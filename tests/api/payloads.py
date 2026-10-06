"""Builders for synthetic Tion v4 payloads, following the recovered schema."""

from uuid import UUID

from custom_components.tion.api.datapoints import DPValue, encode_dp_value
from custom_components.tion.api.protobuf import (
    encode_bytes,
    encode_guid,
    encode_int64,
    encode_string,
    encode_varint,
)

PROFILE_4S = UUID("01a0cd78-0d3e-78b5-a357-a0b7d1c7af0b")
PROFILE_3S = UUID("01a08a70-2cf0-7cc6-b310-47107991203a")
PROFILE_O2 = UUID("01a089dc-6ad3-77b7-bf73-36568ad7a03b")
PROFILE_BS310 = UUID("01a08a28-c009-7883-9fc3-dffd5092d10d")
PROFILE_BS410 = UUID("01a08a28-e7ea-7c1d-b2a4-1ed84e12cce8")
PROFILE_CO2 = UUID("01a089dc-82b2-7054-9163-c59be1cce0f5")
PROFILE_CLEVER = UUID("01a089dc-03b8-76e3-80b1-e8722c210851")

LOCATION_ID = UUID("0199aaaa-0000-7000-8000-000000000001")
ROOM_ID = UUID("0199aaaa-0000-7000-8000-000000000002")
USER_ID = UUID("0199aaaa-0000-7000-8000-0000000000ff")
LEGACY_USER = UUID("00000000-0000-0000-0000-000000000001")


def _wrapped_string(field: int, value: str) -> bytes:
    return encode_bytes(field, encode_string(1, value))


def _wrapped_guid(field: int, value: UUID) -> bytes:
    return encode_bytes(field, encode_guid(1, value))


def _wrapped_int(field: int, value: int) -> bytes:
    return encode_bytes(field, encode_int64(1, value))


def auto_control(
    *,
    enabled: bool,
    speed_min: int,
    speed_max: int,
    co2_target: int,
    algorithm: int = 1,
) -> bytes:
    """Encode an AutoControlParamsInfo message."""
    return (
        encode_varint(1, int(enabled))
        + encode_varint(2, speed_min)
        + encode_varint(3, speed_max)
        + encode_varint(4, co2_target)
        + encode_varint(5, algorithm)
    )


def room(
    room_id: UUID = ROOM_ID, name: str = "Bedroom", auto: bytes | None = None
) -> bytes:
    """Encode a RoomInfo message."""
    payload = _wrapped_guid(1, room_id) + _wrapped_string(2, name)
    if auto is not None:
        payload += encode_bytes(7, auto)
    return payload


def device(
    device_id: str,
    profile_id: UUID,
    *,
    name: str = "Device",
    model: int = 0,
    submodel: int = 0,
    room_id: UUID | None = ROOM_ID,
    parent_id: str | None = None,
    is_online: bool = True,
    is_gateway: bool = False,
    firmware: int = 0,
    hardware: int = 0,
    macs: tuple[int, int, int, int] = (0, 0, 0, 0),
    created_by: UUID = USER_ID,
) -> bytes:
    """Encode a DeviceInfo message."""
    payload = (
        _wrapped_string(1, device_id)
        + _wrapped_string(2, name)
        + _wrapped_int(3, model)
        + _wrapped_int(4, submodel)
        + _wrapped_int(5, firmware)
        + _wrapped_int(6, hardware)
        + _wrapped_guid(7, created_by)
        + _wrapped_guid(11, profile_id)
        + encode_varint(15, int(is_online))
        + encode_varint(23, int(is_gateway))
    )
    if room_id is not None:
        payload += _wrapped_guid(10, room_id)
    if parent_id is not None:
        payload += _wrapped_string(20, parent_id)
    for field, mac in zip((16, 17, 18, 19), macs, strict=True):
        payload += encode_bytes(field, encode_varint(1, mac))
    return payload


def location(
    sid: str,
    *,
    location_id: UUID = LOCATION_ID,
    name: str = "Home",
    rooms: tuple[bytes, ...] = (),
    devices: tuple[bytes, ...] = (),
    created_by: UUID = USER_ID,
) -> bytes:
    """Encode a FullStructureLocation message."""
    return (
        _wrapped_guid(1, location_id)
        + _wrapped_string(2, sid)
        + _wrapped_string(3, name)
        + _wrapped_guid(5, created_by)
        + b"".join(encode_bytes(9, item) for item in rooms)
        + b"".join(encode_bytes(10, item) for item in devices)
    )


def structure_response(*locations: bytes, wstoken: str = "ws-token") -> bytes:
    """Encode a successful GetFullStructureLocationsResponse."""
    payload = b"".join(encode_bytes(1, item) for item in locations)
    return encode_bytes(1, payload + encode_string(2, wstoken))


def error_response(code: int, message: str) -> bytes:
    """Encode a {Success = 1 | Err = 2} response carrying Err."""
    return encode_bytes(2, encode_varint(1, code) + encode_string(2, message))


def state_report(device_id: str, *values: DPValue, command_id: int = 0) -> bytes:
    """Encode a DPStateReport."""
    payload = encode_string(1, device_id) + b"".join(
        encode_bytes(4, encode_dp_value(value)) for value in values
    )
    if command_id:
        payload += encode_varint(5, command_id)
    return payload


def update_response(
    command_id: int,
    *values: DPValue,
    success: bool = True,
    error_code: int = 0,
    error_message: str = "",
) -> bytes:
    """Encode a DPUpdateResponse."""
    payload = encode_varint(2, command_id)
    if success:
        payload += encode_varint(3, 1)
    if error_code:
        payload += encode_varint(4, error_code)
    if error_message:
        payload += encode_string(5, error_message)
    return payload + b"".join(
        encode_bytes(6, encode_dp_value(value)) for value in values
    )


def auto_control_changed(
    room_id: UUID,
    *,
    enabled: bool,
    speed_min: int | None = None,
    speed_max: int | None = None,
    co2_target: int | None = None,
    removed: bool = False,
    occurred_at: float | None = None,
) -> bytes:
    """Encode an app.events.AutoControlChanged event; params only with all limits."""
    payload = b""
    if occurred_at is not None:
        seconds = int(occurred_at)
        stamp = encode_varint(1, seconds) + encode_varint(
            2, round((occurred_at - seconds) * 1_000_000_000)
        )
        meta = encode_bytes(1, stamp) + encode_varint(3, 1)
        payload += encode_bytes(1, encode_bytes(1, meta))
    payload += _wrapped_guid(2, room_id) + encode_varint(3, int(enabled))
    if speed_min is not None and speed_max is not None and co2_target is not None:
        params = (
            encode_varint(1, speed_min)
            + encode_varint(2, speed_max)
            + encode_varint(3, co2_target)
            + encode_varint(4, 1)
        )
        payload += encode_bytes(5, params)
    if removed:
        payload += encode_varint(6, 1)
    return payload
