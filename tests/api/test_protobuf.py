"""Tests for the minimal protobuf codec."""

from uuid import UUID

import pytest

from custom_components.tion.api.exceptions import TionApiError
from custom_components.tion.api.protobuf import (
    ProtobufDecodeError,
    ProtoMessage,
    encode_bytes,
    encode_float,
    encode_guid,
    encode_int64,
    encode_packed,
    encode_string,
    encode_varint,
)


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        pytest.param(0, b"\x08\x00", id="zero"),
        pytest.param(1, b"\x08\x01", id="one"),
        pytest.param(300, b"\x08\xac\x02", id="two_bytes"),
        pytest.param(2**64 - 1, b"\x08" + b"\xff" * 9 + b"\x01", id="uint64_max"),
    ],
)
def test_encode_varint_known_bytes(value: int, encoded: bytes) -> None:
    """Varints match the protobuf reference encoding."""
    assert encode_varint(1, value) == encoded


def test_encode_varint_rejects_negative() -> None:
    """Negative values are not representable in our unsigned fields."""
    with pytest.raises(ValueError):
        encode_varint(1, -1)


def test_encode_string_and_bytes_known_bytes() -> None:
    """Length-delimited fields carry tag, length and payload."""
    assert encode_string(2, "hi") == b"\x12\x02hi"
    assert encode_bytes(3, b"\x00\xff") == b"\x1a\x02\x00\xff"


def test_roundtrip_with_nested_message_and_repeats() -> None:
    """Decoding returns what was encoded, nested messages decode on demand."""
    nested = encode_string(1, "user@example.com")
    payload = (
        encode_bytes(2, nested)
        + encode_varint(6, 5)
        + encode_string(7, "first")
        + encode_string(7, "second")
    )

    message = ProtoMessage.parse(payload)

    assert message.get_int(6) == 5
    assert message.get_str(7) == "first"
    inner = message.get_message(2)
    assert inner is not None
    assert inner.get_str(1) == "user@example.com"


def test_missing_fields_return_defaults() -> None:
    """Absent proto3 fields read as their defaults."""
    message = ProtoMessage.parse(b"")

    assert not message.has(1)
    assert message.get_int(1) == 0
    assert message.get_str(1) == ""
    assert message.get_bytes(1) == b""
    assert message.get_message(1) is None


def test_fixed_width_fields_are_skipped_over() -> None:
    """fixed64/fixed32 fields (e.g. .NET Guid halves) do not break parsing."""
    payload = b"\x09" + b"\x01" * 8 + b"\x15" + b"\x02" * 4 + encode_varint(3, 7)

    assert ProtoMessage.parse(payload).get_int(3) == 7


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"\x08", id="truncated_varint"),
        pytest.param(b"\x12\x05ab", id="truncated_bytes"),
        pytest.param(b"\x08" + b"\xff" * 11, id="varint_too_long"),
        pytest.param(b"\x0b", id="group_wire_type"),
        pytest.param(b"\x00\x01", id="field_zero"),
        pytest.param(b"\x09\x01\x02", id="truncated_fixed64"),
    ],
)
def test_malformed_input_raises_decode_error(payload: bytes) -> None:
    """Malformed bytes raise a TionApiError subclass, never IndexError."""
    with pytest.raises(ProtobufDecodeError):
        ProtoMessage.parse(payload)


def test_wrong_wire_type_access_raises() -> None:
    """Reading a varint field as bytes (or vice versa) is a decode error."""
    message = ProtoMessage.parse(encode_varint(1, 5) + encode_string(2, "x"))

    with pytest.raises(ProtobufDecodeError):
        message.get_bytes(1)
    with pytest.raises(ProtobufDecodeError):
        message.get_int(2)


def test_invalid_utf8_string_raises() -> None:
    """A non-UTF-8 string field is a decode error."""
    message = ProtoMessage.parse(encode_bytes(1, b"\xff\xfe"))

    with pytest.raises(ProtobufDecodeError):
        message.get_str(1)


def test_decode_error_is_api_error() -> None:
    """Callers can treat malformed responses as API errors."""
    assert issubclass(ProtobufDecodeError, TionApiError)


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        pytest.param(150, b"\x08\x96\x01", id="positive"),
        pytest.param(-1, b"\x08" + b"\xff" * 9 + b"\x01", id="minus_one"),
    ],
)
def test_encode_int64_known_bytes(value: int, encoded: bytes) -> None:
    """Signed int64 uses two's complement in a ten-byte varint."""
    assert encode_int64(1, value) == encoded


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(0, id="zero"),
        pytest.param(-150, id="negative"),
        pytest.param(2**63 - 1, id="int64_max"),
        pytest.param(-(2**63), id="int64_min"),
    ],
)
def test_int64_roundtrip(value: int) -> None:
    """Signed values survive encode and decode."""
    assert ProtoMessage.parse(encode_int64(2, value)).get_sint64(2) == value


def test_float_known_bytes_and_roundtrip() -> None:
    """Floats are little-endian fixed32."""
    assert encode_float(7, 1.5) == b"\x3d\x00\x00\xc0\x3f"
    assert ProtoMessage.parse(encode_float(7, 21.5)).get_float(7) == 21.5


def test_get_float_rejects_wider_value() -> None:
    """A value wider than 32 bits is not a float."""
    with pytest.raises(ProtobufDecodeError):
        ProtoMessage.parse(encode_varint(7, 2**40)).get_float(7)


@pytest.mark.parametrize(
    ("values", "encoded"),
    [
        # DPStateQuery{dp_ids_to_query=[10]} as recorded from the Tion app.
        pytest.param([10], b"\x1a\x01\x0a", id="recorded_query"),
        pytest.param([100, 300], b"\x1a\x03\x64\xac\x02", id="multi_byte"),
    ],
)
def test_encode_packed_known_bytes(values: list[int], encoded: bytes) -> None:
    """Packed repeated varints share one length-delimited field."""
    assert encode_packed(3, values) == encoded


def test_get_packed_accepts_packed_and_unpacked() -> None:
    """Decoders must accept both encodings of a repeated scalar."""
    message = ProtoMessage.parse(
        encode_varint(3, 10) + encode_varint(3, 11) + encode_packed(3, [12, 13])
    )

    assert message.get_packed(3) == [10, 11, 12, 13]


def test_repeated_getters_keep_wire_order() -> None:
    """get_all and get_messages return every value in order."""
    message = ProtoMessage.parse(
        encode_bytes(4, encode_varint(1, 7))
        + encode_bytes(4, encode_varint(1, 8))
        + encode_varint(5, 1)
        + encode_varint(5, 2)
    )

    assert [item.get_int(1) for item in message.get_messages(4)] == [7, 8]
    assert message.get_all(5) == [1, 2]
    assert message.get_all(6) == []
    assert message.get_messages(6) == []


def test_get_messages_rejects_scalars() -> None:
    """A varint field cannot be read as messages."""
    with pytest.raises(ProtobufDecodeError):
        ProtoMessage.parse(encode_varint(4, 1)).get_messages(4)


def test_get_map_reads_entries_with_default_key() -> None:
    """Map entries are repeated {1: key, 2: value}; a zero key may be omitted."""
    message = ProtoMessage.parse(
        encode_bytes(1, encode_string(2, "on"))
        + encode_bytes(1, encode_varint(1, 2) + encode_string(2, "beeperOn"))
    )

    assert message.get_map(1) == {0: "on", 2: "beeperOn"}


def test_get_bool() -> None:
    """Bools are varints."""
    message = ProtoMessage.parse(encode_varint(3, 1))

    assert message.get_bool(3) is True
    assert message.get_bool(4) is False


def test_guid_known_bytes() -> None:
    """A .NET Guid is two little-endian fixed64 halves of UUID.bytes_le."""
    value = UUID("00112233-4455-6677-8899-aabbccddeeff")
    # Tag+length of field 1, then fixed64 lo (tag 0x09) and fixed64 hi (tag 0x11).
    encoded = bytes.fromhex("0a12093322110055447766118899aabbccddeeff")

    assert encode_guid(1, value) == encoded
    assert ProtoMessage.parse(encoded).get_guid(1) == value


def test_get_guid_absent_is_none() -> None:
    """A missing Guid field reads as None."""
    assert ProtoMessage.parse(b"").get_guid(1) is None


@pytest.mark.parametrize(
    "payload",
    [
        # Oversized varint: 10 bytes with high bit set on last byte encodes 70 bits
        pytest.param(b"\x08" + b"\xff" * 9 + b"\x7f", id="oversized_varint"),
    ],
)
def test_oversized_varint_raises(payload: bytes) -> None:
    """A varint wider than 64 bits is malformed."""
    with pytest.raises(ProtobufDecodeError):
        ProtoMessage.parse(payload)
