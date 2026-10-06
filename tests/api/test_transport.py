"""Tests for the Tion v4 gRPC-Web transport."""

import ssl
from types import SimpleNamespace
from typing import Any, Self
from uuid import UUID

from aiohttp import ClientConnectionError
from multidict import CIMultiDict
import pytest

from custom_components.tion.api import transport
from custom_components.tion.api.exceptions import (
    TionApiError,
    TionAuthError,
    TionConnectionError,
)
from custom_components.tion.api.transport import (
    API_URL,
    TionTransport,
    create_ssl_context,
    frame,
    unframe,
)

NOW = 1_790_000_000.0
SSL_CONTEXT = ssl.create_default_context()


def _trailers(text: str) -> bytes:
    raw = text.encode()
    return b"\x80" + len(raw).to_bytes(4, "big") + raw


class FakeResponse:
    """Async-context-manager stand-in for an aiohttp response."""

    def __init__(
        self, status: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        """Store the canned response."""
        self.status = status
        self.headers = CIMultiDict(headers or {})
        self._body = body

    async def read(self) -> bytes:
        """Return the canned body."""
        return self._body

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(self, *exc: object) -> bool:
        """Exit the async context."""
        return False


class FakeSession:
    """Records posts and replies with a canned response or exception."""

    def __init__(self, result: FakeResponse | Exception) -> None:
        """Store the canned result."""
        self.result = result
        self.calls: list[SimpleNamespace] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record the call and return the canned response."""
        self.calls.append(SimpleNamespace(url=url, **kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _ok(
    body: bytes = b"\x08\x01", headers: dict[str, str] | None = None
) -> FakeResponse:
    return FakeResponse(200, frame(body) + _trailers("grpc-status: 0"), headers)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the local clock so server-time offsets are exact."""
    monkeypatch.setattr(transport.time, "time", lambda: NOW)


def test_frame_known_bytes() -> None:
    """A data frame is flag 0, big-endian length, then the message."""
    assert frame(b"\x08\x01") == b"\x00\x00\x00\x00\x02\x08\x01"


def test_unframe_joins_data_and_parses_trailers() -> None:
    """Data frames are concatenated; trailer names are lower-cased."""
    body = (
        frame(b"\x08")
        + frame(b"\x01")
        + _trailers("Grpc-Status: 0\r\ngrpc-message: ok\r\n")
    )

    data, trailers = unframe(body)

    assert data == b"\x08\x01"
    assert trailers == {"grpc-status": "0", "grpc-message": "ok"}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"\x00\x00\x00", id="truncated_header"),
        pytest.param(b"\x00\x00\x00\x00\x05\x08", id="truncated_frame"),
        pytest.param(b"\x01\x00\x00\x00\x01\x08", id="compressed_frame"),
    ],
)
def test_unframe_rejects_malformed_body(body: bytes) -> None:
    """Broken framing is an API error, not an IndexError or silent garbage."""
    with pytest.raises(TionApiError):
        unframe(body)


@pytest.mark.asyncio
async def test_call_posts_framed_request_with_headers() -> None:
    """The request goes to service/method with gRPC-Web headers and our SSL."""
    session = FakeSession(_ok())
    client = TionTransport(session, SSL_CONTEXT)

    data = await client.async_call("pkg.Service", "Method", b"\x10\x02", "jwt")

    assert data == b"\x08\x01"
    call = session.calls[0]
    assert call.url == f"{API_URL}/pkg.Service/Method"
    assert call.data == frame(b"\x10\x02")
    assert call.ssl is SSL_CONTEXT
    assert call.headers == {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "accept-language": "ru",
        "authorization": "Bearer jwt",
    }


@pytest.mark.asyncio
async def test_call_without_token_sends_no_authorization() -> None:
    """Login calls are anonymous."""
    session = FakeSession(_ok())

    await TionTransport(session, SSL_CONTEXT).async_call("s", "m", b"")

    assert "authorization" not in session.calls[0].headers


@pytest.mark.asyncio
async def test_call_sends_location_id_header() -> None:
    """A location-scoped call carries the location UUID as a lowercase string."""
    session = FakeSession(_ok())
    location_id = UUID("0A1B2C3D-0000-4000-8000-00000000ABCD")

    await TionTransport(session, SSL_CONTEXT).async_call(
        "s", "m", b"", "jwt", location_id=location_id
    )

    assert session.calls[0].headers["locationid"] == (
        "0a1b2c3d-0000-4000-8000-00000000abcd"
    )


@pytest.mark.asyncio
async def test_call_without_location_id_sends_no_header() -> None:
    """Calls that are not location-scoped send no locationid header."""
    session = FakeSession(_ok())

    await TionTransport(session, SSL_CONTEXT).async_call("s", "m", b"", "jwt")

    assert "locationid" not in session.calls[0].headers


@pytest.mark.asyncio
async def test_trailers_only_status_in_headers() -> None:
    """An error sent as headers only (empty body) is still recognized."""
    response = FakeResponse(
        200, b"", {"grpc-status": "16", "grpc-message": "token%20expired"}
    )

    with pytest.raises(TionAuthError, match="token expired"):
        await TionTransport(FakeSession(response), SSL_CONTEXT).async_call(
            "s", "m", b""
        )


@pytest.mark.parametrize(
    ("response", "error"),
    [
        pytest.param(FakeResponse(401, b""), TionAuthError, id="http_401"),
        pytest.param(FakeResponse(503, b""), TionConnectionError, id="http_503"),
        pytest.param(FakeResponse(403, b""), TionApiError, id="http_403"),
        pytest.param(FakeResponse(200, frame(b"")), TionApiError, id="no_status"),
        pytest.param(
            FakeResponse(200, _trailers("grpc-status: abc")), TionApiError, id="bad"
        ),
        pytest.param(
            FakeResponse(200, _trailers("grpc-status: 16")),
            TionAuthError,
            id="unauthenticated",
        ),
        pytest.param(
            FakeResponse(200, _trailers("grpc-status: 14")),
            TionConnectionError,
            id="unavailable",
        ),
        pytest.param(
            FakeResponse(200, _trailers("grpc-status: 4")),
            TionConnectionError,
            id="deadline",
        ),
        pytest.param(
            FakeResponse(200, _trailers("grpc-status: 2")), TionApiError, id="unknown"
        ),
    ],
)
@pytest.mark.asyncio
async def test_call_normalizes_errors(
    response: FakeResponse, error: type[Exception]
) -> None:
    """HTTP and gRPC failures map onto the three Tion error types."""
    with pytest.raises(error):
        await TionTransport(FakeSession(response), SSL_CONTEXT).async_call(
            "s", "m", b""
        )


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(ClientConnectionError("tls"), id="client_error"),
        pytest.param(TimeoutError(), id="timeout"),
    ],
)
@pytest.mark.asyncio
async def test_network_failures_are_connection_errors(exc: Exception) -> None:
    """Transport-level failures (incl. TLS) are connection errors."""
    with pytest.raises(TionConnectionError):
        await TionTransport(FakeSession(exc), SSL_CONTEXT).async_call("s", "m", b"")


@pytest.mark.parametrize(
    ("headers", "offset"),
    [
        pytest.param(
            {"x-server-time-ms": str(int((NOW + 42.5) * 1000))}, 42.5, id="server_ms"
        ),
        pytest.param(
            {"Date": "Thu, 24 Sep 2026 08:03:32 GMT"}, 1790237012 - NOW, id="date"
        ),
        pytest.param(
            {"x-server-time-ms": "garbage", "Date": "Thu, 24 Sep 2026 08:03:32 GMT"},
            1790237012 - NOW,
            id="bad_ms_falls_back_to_date",
        ),
    ],
)
@pytest.mark.asyncio
async def test_server_time_offset_from_headers(
    headers: dict[str, str], offset: float
) -> None:
    """The server clock is read from x-server-time-ms, else Date."""
    client = TionTransport(FakeSession(_ok(headers=headers)), SSL_CONTEXT)

    await client.async_call("s", "m", b"")

    assert client.server_time_offset == pytest.approx(offset)
    assert client.server_time() == pytest.approx(NOW + offset)


@pytest.mark.asyncio
async def test_malformed_time_headers_keep_previous_offset() -> None:
    """Unparseable clock headers never break the call or reset the offset."""
    client = TionTransport(
        FakeSession(_ok(headers={"x-server-time-ms": "x", "Date": "not a date"})),
        SSL_CONTEXT,
    )
    client.server_time_offset = 7.0

    await client.async_call("s", "m", b"")

    assert client.server_time_offset == 7.0


@pytest.mark.asyncio
async def test_server_time_is_read_from_error_responses_too() -> None:
    """A rejected call still re-syncs the clock (RenewAccess retry relies on it)."""
    response = FakeResponse(
        200,
        _trailers("grpc-status: 2"),
        {"x-server-time-ms": str(int(NOW * 1000) + 5000)},
    )
    client = TionTransport(FakeSession(response), SSL_CONTEXT)

    with pytest.raises(TionApiError):
        await client.async_call("s", "m", b"")

    assert client.server_time_offset == pytest.approx(5.0)


def test_ssl_context_verifies_and_trusts_original_intermediate() -> None:
    """Verification stays on and the original GlobalSign intermediate is trusted."""
    context = create_ssl_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    common_names = {
        value
        for ca in context.get_ca_certs()
        for rdn in ca["subject"]
        for key, value in rdn
        if key == "commonName"
    }
    assert "GlobalSign GCC R3 DV TLS CA 2020" in common_names
