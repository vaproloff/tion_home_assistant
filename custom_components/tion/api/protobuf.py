"""Minimal protobuf wire codec for the Tion v4 account messages."""

import struct

from .exceptions import TionApiError

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5

_MAX_VARINT_BYTES = 10


class ProtobufDecodeError(TionApiError):
    """Raised when bytes are not a well-formed protobuf message."""


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative varints are not supported")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def encode_varint(field: int, value: int) -> bytes:
    """Encode a varint field (uint32/uint64/bool/enum)."""
    return _tag(field, WIRE_VARINT) + _varint(value)


def encode_bytes(field: int, value: bytes) -> bytes:
    """Encode a length-delimited field (bytes or a nested message)."""
    return _tag(field, WIRE_BYTES) + _varint(len(value)) + value


def encode_string(field: int, value: str) -> bytes:
    """Encode a UTF-8 string field."""
    return encode_bytes(field, value.encode())


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for index in range(_MAX_VARINT_BYTES):
        if pos >= len(buf):
            raise ProtobufDecodeError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, pos
    raise ProtobufDecodeError("varint too long")


class ProtoMessage:
    """One decoded message level: field number -> list of raw values."""

    def __init__(self, fields: dict[int, list[int | bytes]]) -> None:
        """Wrap decoded fields."""
        self._fields = fields

    @classmethod
    def parse(cls, buf: bytes) -> ProtoMessage:
        """Decode one message level; nested messages stay as bytes."""
        fields: dict[int, list[int | bytes]] = {}
        pos = 0
        while pos < len(buf):
            key, pos = _read_varint(buf, pos)
            field, wire_type = key >> 3, key & 0x07
            if field == 0:
                raise ProtobufDecodeError("field number 0")
            value: int | bytes
            if wire_type == WIRE_VARINT:
                value, pos = _read_varint(buf, pos)
            elif wire_type == WIRE_BYTES:
                length, pos = _read_varint(buf, pos)
                if pos + length > len(buf):
                    raise ProtobufDecodeError(f"truncated field {field}")
                value = buf[pos : pos + length]
                pos += length
            elif wire_type == WIRE_FIXED64:
                if pos + 8 > len(buf):
                    raise ProtobufDecodeError(f"truncated field {field}")
                value = struct.unpack_from("<Q", buf, pos)[0]
                pos += 8
            elif wire_type == WIRE_FIXED32:
                if pos + 4 > len(buf):
                    raise ProtobufDecodeError(f"truncated field {field}")
                value = struct.unpack_from("<I", buf, pos)[0]
                pos += 4
            else:
                raise ProtobufDecodeError(f"unsupported wire type {wire_type}")
            fields.setdefault(field, []).append(value)
        return cls(fields)

    def has(self, field: int) -> bool:
        """Return True if the field is present."""
        return field in self._fields

    def get_int(self, field: int, default: int = 0) -> int:
        """Return the first value of a numeric field."""
        if field not in self._fields:
            return default
        value = self._fields[field][0]
        if not isinstance(value, int):
            raise ProtobufDecodeError(f"field {field} is not numeric")
        return value

    def get_bytes(self, field: int, default: bytes = b"") -> bytes:
        """Return the first value of a length-delimited field."""
        if field not in self._fields:
            return default
        value = self._fields[field][0]
        if not isinstance(value, bytes):
            raise ProtobufDecodeError(f"field {field} is not length-delimited")
        return value

    def get_str(self, field: int, default: str = "") -> str:
        """Return the first value of a string field."""
        if field not in self._fields:
            return default
        try:
            return self.get_bytes(field).decode()
        except UnicodeDecodeError as err:
            raise ProtobufDecodeError(f"field {field} is not UTF-8") from err

    def get_message(self, field: int) -> ProtoMessage | None:
        """Return the first value of a nested message field, decoded."""
        if field not in self._fields:
            return None
        return ProtoMessage.parse(self.get_bytes(field))
