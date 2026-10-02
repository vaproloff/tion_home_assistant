"""Tests for the minimal protobuf codec."""

import pytest

from custom_components.tion.exceptions import TionApiError
from custom_components.tion.protobuf import (
    ProtobufDecodeError,
    ProtoMessage,
    encode_bytes,
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
