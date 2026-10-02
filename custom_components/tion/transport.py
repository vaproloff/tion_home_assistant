"""gRPC-Web transport for the Tion v4 cloud."""

from collections.abc import Mapping
import contextlib
from email.utils import parsedate_to_datetime
from functools import cache
import ssl
import struct
import time
from urllib.parse import unquote

from aiohttp import ClientError, ClientSession, ClientTimeout
import certifi

from .exceptions import TionApiError, TionAuthError, TionConnectionError

API_URL = "https://api-v4.magicair.tion.ru:5000"
REQUEST_TIMEOUT = 30

GRPC_OK = 0
GRPC_DEADLINE_EXCEEDED = 4
GRPC_UNAVAILABLE = 14
GRPC_UNAUTHENTICATED = 16
_GRPC_CONNECTION_STATUSES = frozenset({GRPC_DEADLINE_EXCEEDED, GRPC_UNAVAILABLE})

_FRAME_HEADER = struct.Struct(">BI")
_FLAG_COMPRESSED = 0x01
_FLAG_TRAILERS = 0x80

# Tion's servers send a copy of this intermediate with a corrupted signature;
# trusting the original lets OpenSSL build the chain with verification on.
# Valid until 2029-03-18; issued by GlobalSign Root CA - R3 (in certifi).
GLOBALSIGN_GCC_R3_DV_TLS_CA_2020 = """\
-----BEGIN CERTIFICATE-----
MIIEsDCCA5igAwIBAgIQd70OB0LV2enQSdd00CpvmjANBgkqhkiG9w0BAQsFADBM
MSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEGA1UEChMKR2xv
YmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjAeFw0yMDA3MjgwMDAwMDBaFw0y
OTAzMTgwMDAwMDBaMFMxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWdu
IG52LXNhMSkwJwYDVQQDEyBHbG9iYWxTaWduIEdDQyBSMyBEViBUTFMgQ0EgMjAy
MDCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKxnlJV/de+OpwyvCXAJ
IcxPCqkFPh1lttW2oljS3oUqPKq8qX6m7K0OVKaKG3GXi4CJ4fHVUgZYE6HRdjqj
hhnuHY6EBCBegcUFgPG0scB12Wi8BHm9zKjWxo3Y2bwhO8Fvr8R42pW0eINc6OTb
QXC0VWFCMVzpcqgz6X49KMZowAMFV6XqtItcG0cMS//9dOJs4oBlpuqX9INxMTGp
6EASAF9cnlAGy/RXkVS9nOLCCa7pCYV+WgDKLTF+OK2Vxw3RUJ/p8009lQeUARv2
UCcNNPCifYX1xIspvarkdjzLwzOdLahDdQbJON58zN4V+lMj0msg+c0KnywPIRp3
BMkCAwEAAaOCAYUwggGBMA4GA1UdDwEB/wQEAwIBhjAdBgNVHSUEFjAUBggrBgEF
BQcDAQYIKwYBBQUHAwIwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFgQUDZjA
c3+rvb3ZR0tJrQpKDKw+x3wwHwYDVR0jBBgwFoAUj/BLf6guRSSuTVD6Y5qL3uLd
G7wwewYIKwYBBQUHAQEEbzBtMC4GCCsGAQUFBzABhiJodHRwOi8vb2NzcDIuZ2xv
YmFsc2lnbi5jb20vcm9vdHIzMDsGCCsGAQUFBzAChi9odHRwOi8vc2VjdXJlLmds
b2JhbHNpZ24uY29tL2NhY2VydC9yb290LXIzLmNydDA2BgNVHR8ELzAtMCugKaAn
hiVodHRwOi8vY3JsLmdsb2JhbHNpZ24uY29tL3Jvb3QtcjMuY3JsMEcGA1UdIARA
MD4wPAYEVR0gADA0MDIGCCsGAQUFBwIBFiZodHRwczovL3d3dy5nbG9iYWxzaWdu
LmNvbS9yZXBvc2l0b3J5LzANBgkqhkiG9w0BAQsFAAOCAQEAy8j/c550ea86oCkf
r2W+ptTCYe6iVzvo7H0V1vUEADJOWelTv07Obf+YkEatdN1Jg09ctgSNv2h+LMTk
KRZdAXmsE3N5ve+z1Oa9kuiu7284LjeS09zHJQB4DJJJkvtIbjL/ylMK1fbMHhAW
i0O194TWvH3XWZGXZ6ByxTUIv1+kAIql/Mt29PmKraTT5jrzcVzQ5A9jw16yysuR
XRrLODlkS1hyBjsfyTNZrmL1h117IFgntBA5SQNVl9ckedq5r4RSAU85jV8XK5UL
REjRZt2I6M9Po9QL7guFLu4sPFJpwR1sPJvubS2THeo7SxYoNDtdyBHs7euaGcMa
D/fayQ==
-----END CERTIFICATE-----
"""


@cache
def create_ssl_context() -> ssl.SSLContext:
    """Return the SSL context for Tion hosts. Blocking: run in an executor."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=GLOBALSIGN_GCC_R3_DV_TLS_CA_2020)
    return context


def frame(payload: bytes) -> bytes:
    """Wrap a protobuf message in a gRPC-Web data frame."""
    return _FRAME_HEADER.pack(0, len(payload)) + payload


def unframe(body: bytes) -> tuple[bytes, dict[str, str]]:
    """Split a gRPC-Web response body into the message bytes and trailers."""
    data = bytearray()
    trailers: dict[str, str] = {}
    pos = 0
    while pos < len(body):
        if pos + _FRAME_HEADER.size > len(body):
            raise TionApiError("Truncated gRPC-Web frame header")
        flags, length = _FRAME_HEADER.unpack_from(body, pos)
        pos += _FRAME_HEADER.size
        if pos + length > len(body):
            raise TionApiError("Truncated gRPC-Web frame")
        chunk = body[pos : pos + length]
        pos += length
        if flags & _FLAG_COMPRESSED:
            raise TionApiError("Compressed gRPC-Web frames are not supported")
        if flags & _FLAG_TRAILERS:
            for line in chunk.decode(errors="replace").split("\r\n"):
                name, sep, value = line.partition(":")
                if sep:
                    trailers[name.strip().lower()] = value.strip()
        else:
            data += chunk
    return bytes(data), trailers


class TionTransport:
    """Unary gRPC-Web calls to the Tion v4 API."""

    def __init__(
        self,
        session: ClientSession,
        ssl_context: ssl.SSLContext,
        base_url: str = API_URL,
    ) -> None:
        """Initialize the transport."""
        self._session = session
        self._ssl_context = ssl_context
        self._base_url = base_url
        self.server_time_offset = 0.0

    def server_time(self) -> float:
        """Return the current Unix time on the server's clock."""
        return time.time() + self.server_time_offset

    async def async_call(
        self, service: str, method: str, payload: bytes, token: str | None = None
    ) -> bytes:
        """Call a unary RPC and return the response message bytes."""
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "accept-language": "ru",
        }
        if token:
            headers["authorization"] = f"Bearer {token}"
        try:
            async with self._session.post(
                f"{self._base_url}/{service}/{method}",
                data=frame(payload),
                headers=headers,
                ssl=self._ssl_context,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                self._update_server_time(response.headers)
                status = response.status
                response_headers = dict(response.headers)
                body = await response.read()
        except (ClientError, TimeoutError) as err:
            raise TionConnectionError(f"{method}: {err!r}") from err

        if status == 401:
            raise TionAuthError(f"{method}: HTTP 401")
        if status >= 500:
            raise TionConnectionError(f"{method}: HTTP {status}")
        if status != 200:
            raise TionApiError(f"{method}: HTTP {status}")

        data, trailers = unframe(body)
        # A trailers-only error response carries the status in the headers.
        trailers = {
            **{key.lower(): value for key, value in response_headers.items()},
            **trailers,
        }
        if (raw_status := trailers.get("grpc-status")) is None:
            raise TionApiError(f"{method}: response without grpc-status")
        try:
            grpc_status = int(raw_status)
        except ValueError as err:
            raise TionApiError(f"{method}: bad grpc-status {raw_status!r}") from err
        if grpc_status == GRPC_OK:
            return data

        error = (
            f"{method}: grpc-status {grpc_status} "
            f"{unquote(trailers.get('grpc-message', ''))}"
        )
        if grpc_status == GRPC_UNAUTHENTICATED:
            raise TionAuthError(error)
        if grpc_status in _GRPC_CONNECTION_STATUSES:
            raise TionConnectionError(error)
        raise TionApiError(error)

    def _update_server_time(self, headers: Mapping[str, str]) -> None:
        if (server_time := _server_time(headers)) is not None:
            self.server_time_offset = server_time - time.time()


def _server_time(headers: Mapping[str, str]) -> float | None:
    """Return the server clock from response headers, if present and valid."""
    if (server_ms := headers.get("x-server-time-ms")) is not None:
        with contextlib.suppress(ValueError):
            return int(server_ms) / 1000
    if (date := headers.get("Date")) is not None:
        with contextlib.suppress(TypeError, ValueError):
            return parsedate_to_datetime(date).timestamp()
    return None
