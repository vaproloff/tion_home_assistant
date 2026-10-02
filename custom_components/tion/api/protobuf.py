"""Minimal protobuf wire codec for the Tion v4 messages."""

from collections.abc import Iterable
import struct
from uuid import UUID

from .exceptions import TionApiError

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5

_MAX_VARINT_BYTES = 10
_UINT64_MASK = (1 << 64) - 1
_INT64_SIGN = 1 << 63
_GUID = struct.Struct("<QQ")


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


def encode_int64(field: int, value: int) -> bytes:
    """Encode a signed int64 field; negatives take ten bytes (two's complement)."""
    return _tag(field, WIRE_VARINT) + _varint(value & _UINT64_MASK)


def encode_float(field: int, value: float) -> bytes:
    """Encode a 32-bit float field."""
    return _tag(field, WIRE_FIXED32) + struct.pack("<f", value)


def encode_packed(field: int, values: Iterable[int]) -> bytes:
    """Encode a packed repeated varint field."""
    return encode_bytes(field, b"".join(_varint(value) for value in values))


def encode_guid(field: int, value: UUID) -> bytes:
    """Encode a .NET Guid message (bcl.Guid: fixed64 lo = 1, fixed64 hi = 2)."""
    lo, hi = _GUID.unpack(value.bytes_le)
    message = (
        _tag(1, WIRE_FIXED64)
        + struct.pack("<Q", lo)
        + _tag(2, WIRE_FIXED64)
        + struct.pack("<Q", hi)
    )
    return encode_bytes(field, message)


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

    def get_bool(self, field: int, default: bool = False) -> bool:
        """Return the first value of a bool field."""
        return bool(self.get_int(field, int(default)))

    def get_sint64(self, field: int, default: int = 0) -> int:
        """Return the first value of a signed int64 field."""
        value = self.get_int(field, default & _UINT64_MASK)
        return value - (1 << 64) if value & _INT64_SIGN else value

    def get_float(self, field: int, default: float = 0.0) -> float:
        """Return the first value of a 32-bit float field."""
        if field not in self._fields:
            return default
        raw = self.get_int(field)
        if raw > 0xFFFFFFFF:
            raise ProtobufDecodeError(f"field {field} is not a 32-bit float")
        return struct.unpack("<f", struct.pack("<I", raw))[0]

    def get_all(self, field: int) -> list[int | bytes]:
        """Return every value of a repeated field, in wire order."""
        return list(self._fields.get(field, []))

    def get_messages(self, field: int) -> list[ProtoMessage]:
        """Return every value of a repeated message field, decoded."""
        messages = []
        for value in self._fields.get(field, []):
            if not isinstance(value, bytes):
                raise ProtobufDecodeError(f"field {field} is not length-delimited")
            messages.append(ProtoMessage.parse(value))
        return messages

    def get_packed(self, field: int) -> list[int]:
        """Return a repeated varint field, accepting packed and unpacked values."""
        values: list[int] = []
        for value in self._fields.get(field, []):
            if isinstance(value, int):
                values.append(value)
                continue
            pos = 0
            while pos < len(value):
                item, pos = _read_varint(value, pos)
                values.append(item)
        return values

    def get_map(self, field: int) -> dict[int, str]:
        """Return a map<uint32, string> field (repeated {1: key, 2: value})."""
        return {
            entry.get_int(1): entry.get_str(2) for entry in self.get_messages(field)
        }

    def get_guid(self, field: int) -> UUID | None:
        """Return a .NET Guid message field as a UUID, or None if absent."""
        if (message := self.get_message(field)) is None:
            return None
        return UUID(bytes_le=_GUID.pack(message.get_int(1), message.get_int(2)))
