"""Device datapoint messages carried over NATS (package iot.device.messaging)."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
import secrets

from .protobuf import (
    ProtoMessage,
    encode_bytes,
    encode_float,
    encode_int64,
    encode_packed,
    encode_string,
    encode_varint,
)

SERVICE_STATE = "dps"
SERVICE_UPDATE = "dpu"

_DP_ID = 8
_VALID = 10


class DPKind(IntEnum):
    """DPValue oneof members, numbered as their wire fields."""

    BOOL = 1
    INT = 2
    ENUM = 3
    STRING = 4
    RAW = 5
    FLAGS = 6
    FLOAT = 7


type DPRaw = bool | int | str | bytes | float


@dataclass(frozen=True, slots=True)
class DPValue:
    """One datapoint value as sent or reported by a device."""

    dp_id: int
    kind: DPKind
    value: DPRaw
    valid: int = 1


@dataclass(frozen=True, slots=True)
class DPStateReport:
    """A device's report of datapoint values (pushed or answering a query)."""

    device_id: str
    original_command_id: int
    dps: tuple[DPValue, ...]


@dataclass(frozen=True, slots=True)
class DPUpdateResponse:
    """A device's answer to a DPUpdateRequest."""

    original_command_id: int
    success: bool
    error_code: int
    error_message: str
    dps: tuple[DPValue, ...]


def encode_dp_value(dp: DPValue) -> bytes:
    """Encode a DPValue the way the Tion app does: value, dp_id, valid."""
    match dp.kind:
        case DPKind.BOOL:
            value = encode_varint(DPKind.BOOL, int(bool(dp.value)))
        case DPKind.INT:
            value = encode_int64(DPKind.INT, int(dp.value))
        case DPKind.ENUM | DPKind.FLAGS:
            value = encode_varint(dp.kind, int(dp.value))
        case DPKind.STRING:
            value = encode_string(DPKind.STRING, str(dp.value))
        case DPKind.RAW:
            value = encode_bytes(DPKind.RAW, bytes(dp.value))
        case DPKind.FLOAT:
            value = encode_float(DPKind.FLOAT, float(dp.value))
    payload = value + encode_varint(_DP_ID, dp.dp_id)
    if dp.valid:
        payload += encode_varint(_VALID, dp.valid)
    return payload


def decode_dp_value(message: ProtoMessage) -> DPValue | None:
    """Decode a DPValue; None if it carries no value member."""
    for kind in DPKind:
        if message.has(kind):
            return DPValue(
                dp_id=message.get_int(_DP_ID),
                kind=kind,
                value=_read_value(message, kind),
                valid=message.get_int(_VALID),
            )
    return None


def _read_value(message: ProtoMessage, kind: DPKind) -> DPRaw:
    match kind:
        case DPKind.BOOL:
            return message.get_bool(kind)
        case DPKind.INT:
            return message.get_sint64(kind)
        case DPKind.STRING:
            return message.get_str(kind)
        case DPKind.RAW:
            return message.get_bytes(kind)
        case DPKind.FLOAT:
            return message.get_float(kind)
    return message.get_int(kind)


def _decode_values(messages: Iterable[ProtoMessage]) -> tuple[DPValue, ...]:
    return tuple(
        value for message in messages if (value := decode_dp_value(message)) is not None
    )


def encode_state_query(dp_ids: Iterable[int], command_id: int = 0) -> bytes:
    """Encode a DPStateQuery; the device is addressed by the subject."""
    payload = encode_packed(3, dp_ids)
    if command_id:
        payload += encode_varint(4, command_id)
    return payload


def encode_update_request(
    command_id: int, command_timestamp: int, values: Iterable[DPValue]
) -> bytes:
    """Encode a DPUpdateRequest; the device is addressed by the subject."""
    return (
        encode_varint(2, command_id)
        + encode_varint(3, command_timestamp)
        + b"".join(encode_bytes(4, encode_dp_value(value)) for value in values)
    )


def decode_state_report(payload: bytes) -> DPStateReport:
    """Decode a DPStateReport."""
    message = ProtoMessage.parse(payload)
    return DPStateReport(
        device_id=message.get_str(1),
        original_command_id=message.get_int(5),
        dps=_decode_values(message.get_messages(4)),
    )


def decode_update_response(payload: bytes) -> DPUpdateResponse:
    """Decode a DPUpdateResponse."""
    message = ProtoMessage.parse(payload)
    return DPUpdateResponse(
        original_command_id=message.get_int(2),
        success=message.get_bool(3),
        error_code=message.get_int(4),
        error_message=message.get_str(5),
        dps=_decode_values(message.get_messages(6)),
    )


def new_command_id() -> int:
    """Return a random non-zero uint32 to correlate a request with its answer."""
    return secrets.randbelow(2**32 - 1) + 1


def device_subject(location_sid: str, service: str, device_id: str) -> str:
    """Return the subject to publish a request to a device on."""
    return f"hw.rx.{location_sid}.{service}.{device_id}"


def device_reports_subject(location_sid: str) -> str:
    """Return the subscription for every device message of a location."""
    return f"hw.tx.{location_sid}.>"


def location_events_subject(location_sid: str) -> str:
    """Return the subscription for a location's structure events."""
    return f"app.location.{location_sid}.*"


def parse_device_subject(subject: str) -> tuple[str, str, str] | None:
    """Split hw.tx.<location sid>.<service>.<device id>; None if not one."""
    parts = subject.split(".")
    if len(parts) != 5 or parts[:2] != ["hw", "tx"]:
        return None
    return parts[2], parts[3], parts[4]


def parse_location_event(subject: str) -> tuple[str, str] | None:
    """Split app.location.<location sid>.<event>; None if not one."""
    parts = subject.split(".")
    if len(parts) != 4 or parts[:2] != ["app", "location"]:
        return None
    return parts[2], parts[3]
