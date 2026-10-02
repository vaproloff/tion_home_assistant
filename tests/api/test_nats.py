"""Tests for the NATS-over-WebSocket client against a local fake server."""

import asyncio
from collections.abc import AsyncIterator
import json

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer
import pytest
import pytest_asyncio

from custom_components.tion.api.exceptions import (
    TionAuthError,
    TionConnectionError,
    TionError,
)
from custom_components.tion.api.nats import NatsConnection, nats_error

INFO = b'INFO {"server_id":"test","version":"2.14.6","auth_required":true}\r\n'
# A device message as the Tion broker delivers it (MQTT bridge header).
HMSG_HEADERS = b"NATS/1.0\r\nNmqtt-Pub:0\r\n\r\n"


class FakeNatsServer:
    """A scripted NATS server speaking over WebSocket."""

    def __init__(self) -> None:
        """Start with a server that accepts the login."""
        self.url = ""
        self.handshake_reply: bytes | None = b"PONG\r\n"
        self.answer_pings = True
        self.protocol: str | None = None
        self.received = bytearray()
        self.client_frames = asyncio.Queue[bytes]()
        self.ws: web.WebSocketResponse | None = None

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        """Accept the WebSocket and play the script."""
        ws = web.WebSocketResponse(protocols=("maprot",))
        await ws.prepare(request)
        self.ws = ws
        self.protocol = ws.ws_protocol
        await ws.send_bytes(INFO)
        handshake_done = False
        async for message in ws:
            if message.type is not WSMsgType.BINARY:
                continue
            self.received += message.data
            await self.client_frames.put(message.data)
            if not handshake_done and message.data.startswith(b"CONNECT"):
                handshake_done = True
                if self.handshake_reply is None:
                    continue
                await ws.send_bytes(self.handshake_reply)
                if self.handshake_reply.startswith(b"-ERR"):
                    await ws.close()
            elif message.data == b"PING\r\n" and self.answer_pings:
                await ws.send_bytes(b"PONG\r\n")
        return ws

    async def send(self, data: bytes) -> None:
        """Send raw protocol bytes to the client."""
        assert self.ws is not None
        await self.ws.send_bytes(data)

    def connect_options(self) -> dict[str, object]:
        """Return the JSON options of the client's CONNECT."""
        line = bytes(self.received).split(b"\r\n", 1)[0]
        assert line.startswith(b"CONNECT ")
        return json.loads(line.removeprefix(b"CONNECT "))


@pytest_asyncio.fixture
async def server() -> AsyncIterator[FakeNatsServer]:
    """Run a fake NATS server on 127.0.0.1."""
    fake = FakeNatsServer()
    app = web.Application()
    app.router.add_get("/", fake.handler)
    test_server = TestServer(app, host="127.0.0.1")
    await test_server.start_server()
    fake.url = str(test_server.make_url("/")).replace("http://", "ws://")
    yield fake
    await test_server.close()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[ClientSession]:
    """A client HTTP session."""
    async with ClientSession() as client_session:
        yield client_session


class Disconnects:
    """Collects disconnect callbacks."""

    def __init__(self) -> None:
        """Start empty."""
        self.errors: list[TionError] = []
        self.event = asyncio.Event()

    def __call__(self, error: TionError) -> None:
        """Record a disconnect."""
        self.errors.append(error)
        self.event.set()


async def _connect(
    server: FakeNatsServer,
    session: ClientSession,
    disconnects: Disconnects | None = None,
    **kwargs: float,
) -> NatsConnection:
    return await NatsConnection.async_connect(
        session,
        url=server.url,
        user="mappKEYID",
        auth_token="access:wstoken",
        on_disconnect=disconnects or Disconnects(),
        **kwargs,
    )


async def _until(server: FakeNatsServer, data: bytes) -> None:
    """Wait until the server has received these bytes."""
    async with asyncio.timeout(2):
        while data not in server.received:
            await server.client_frames.get()


@pytest.mark.asyncio
async def test_handshake(server: FakeNatsServer, session: ClientSession) -> None:
    """The client speaks maprot and sends the app's CONNECT, then PING."""
    connection = await _connect(server, session)

    assert server.protocol == "maprot"
    assert server.connect_options() == {
        "no_responders": True,
        "protocol": 1,
        "verbose": False,
        "pedantic": False,
        "user": "mappKEYID",
        "auth_token": "access:wstoken",
        "name": "mappV1.1.1225",
        "lang": "nats.ws",
        "version": "3.3.1",
        "headers": True,
    }
    assert bytes(server.received).endswith(b"\r\nPING\r\n")
    assert not connection.closed
    await connection.async_close()


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        pytest.param(
            b"-ERR 'Authorization Violation'\r\n", TionAuthError, id="auth_violation"
        ),
        pytest.param(
            b"-ERR 'User Authentication Expired'\r\n", TionAuthError, id="expired"
        ),
        pytest.param(
            b"-ERR 'Unknown Protocol Operation'\r\n",
            TionConnectionError,
            id="other_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_handshake_rejected(
    server: FakeNatsServer,
    session: ClientSession,
    reply: bytes,
    error: type[TionError],
) -> None:
    """A refused login raises by its kind."""
    server.handshake_reply = reply

    with pytest.raises(error):
        await _connect(server, session)


@pytest.mark.asyncio
async def test_handshake_without_pong_times_out(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """A server that never answers the PING fails the connect."""
    server.handshake_reply = None

    with pytest.raises(TionConnectionError):
        await _connect(server, session, connect_timeout=0.2)


@pytest.mark.asyncio
async def test_unreachable_server(session: ClientSession) -> None:
    """A refused TCP connection is a connection error."""
    with pytest.raises(TionConnectionError):
        await NatsConnection.async_connect(
            session,
            url="ws://127.0.0.1:9/",
            user="u",
            auth_token="t",
            on_disconnect=Disconnects(),
        )


@pytest.mark.asyncio
async def test_messages_reach_subscribers(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """MSG and HMSG are delivered, even split across frames or batched."""
    connection = await _connect(server, session)
    received: list[tuple[str, bytes]] = []
    done = asyncio.Event()

    def on_message(subject: str, payload: bytes) -> None:
        received.append((subject, payload))
        if len(received) == 4:
            done.set()

    sid = await connection.async_subscribe("hw.tx.LOC0000001.>", on_message)
    await _until(server, f"SUB hw.tx.LOC0000001.> {sid}\r\n".encode())

    # A payload containing CRLF must be cut by length, not by line.
    binary = b"\x0a\x0d\x0a\x01"
    await server.send(f"MSG hw.tx.LOC0000001.dps.DEV0000001 {sid} 4\r\n".encode())
    await server.send(binary + b"\r\n")
    hmsg = HMSG_HEADERS + b"\x10\x01"
    await server.send(
        f"HMSG hw.tx.LOC0000001.dpu.DEV0000001 {sid} {len(HMSG_HEADERS)} "
        f"{len(hmsg)}\r\n".encode()
        + hmsg
        + b"\r\n"
        + f"MSG a.b {sid} 1\r\nx\r\nMSG c.d {sid} reply.to 2\r\nyz\r\n".encode()
    )
    async with asyncio.timeout(2):
        await done.wait()

    assert received == [
        ("hw.tx.LOC0000001.dps.DEV0000001", binary),
        ("hw.tx.LOC0000001.dpu.DEV0000001", b"\x10\x01"),
        ("a.b", b"x"),
        ("c.d", b"yz"),
    ]
    await connection.async_close()


@pytest.mark.asyncio
async def test_text_frames_are_accepted(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """Protocol data in WebSocket text frames is read like binary frames."""
    connection = await _connect(server, session)
    received: list[bytes] = []
    done = asyncio.Event()

    def on_message(subject: str, payload: bytes) -> None:
        received.append(payload)
        done.set()

    sid = await connection.async_subscribe("s", on_message)
    await _until(server, b"SUB s ")
    assert server.ws is not None
    await server.ws.send_str(f"MSG s {sid} 2\r\nhi\r\n")
    async with asyncio.timeout(2):
        await done.wait()

    assert received == [b"hi"]
    await connection.async_close()


@pytest.mark.asyncio
async def test_subscriber_error_does_not_stop_reading(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """A failing callback is logged; later messages still arrive."""
    connection = await _connect(server, session)
    received: list[bytes] = []
    done = asyncio.Event()

    def on_message(subject: str, payload: bytes) -> None:
        if payload == b"bad":
            raise RuntimeError("handler bug")
        received.append(payload)
        done.set()

    sid = await connection.async_subscribe("s", on_message)
    await _until(server, b"SUB s ")
    await server.send(f"MSG s {sid} 3\r\nbad\r\nMSG s {sid} 2\r\nok\r\n".encode())
    async with asyncio.timeout(2):
        await done.wait()

    assert received == [b"ok"]
    await connection.async_close()


@pytest.mark.asyncio
async def test_publish_and_unsubscribe_bytes(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """PUB carries the payload length; UNSUB names the subscription."""
    connection = await _connect(server, session)
    sid = await connection.async_subscribe("s", lambda subject, payload: None)

    await connection.async_publish("hw.rx.LOC0000001.dpu.DEV0000001", b"\x10\x01")
    await connection.async_unsubscribe(sid)
    await _until(server, f"UNSUB {sid}\r\n".encode())

    assert b"PUB hw.rx.LOC0000001.dpu.DEV0000001 2\r\n\x10\x01\r\n" in server.received
    await connection.async_close()


@pytest.mark.asyncio
async def test_server_ping_gets_pong(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """The client answers the server's keepalive."""
    connection = await _connect(server, session)
    server.received.clear()

    await server.send(b"PING\r\n")
    await _until(server, b"PONG\r\n")

    await connection.async_close()


@pytest.mark.asyncio
async def test_keepalive_timeout_reports_disconnect(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """Unanswered client PINGs drop the connection."""
    disconnects = Disconnects()
    connection = await _connect(
        server, session, disconnects, ping_interval=0.05, pong_timeout=0.05
    )
    server.answer_pings = False

    async with asyncio.timeout(2):
        await disconnects.event.wait()

    assert [type(error) for error in disconnects.errors] == [TionConnectionError]
    assert connection.closed


@pytest.mark.parametrize(
    ("data", "error"),
    [
        pytest.param(
            b"-ERR 'User Authentication Expired'\r\n", TionAuthError, id="auth"
        ),
        pytest.param(b"-ERR 'Stale Connection'\r\n", TionConnectionError, id="stale"),
    ],
)
@pytest.mark.asyncio
async def test_server_error_reports_disconnect(
    server: FakeNatsServer,
    session: ClientSession,
    data: bytes,
    error: type[TionError],
) -> None:
    """A fatal -ERR after login is reported once, by kind."""
    disconnects = Disconnects()
    await _connect(server, session, disconnects)

    await server.send(data)
    async with asyncio.timeout(2):
        await disconnects.event.wait()

    assert [type(item) for item in disconnects.errors] == [error]


@pytest.mark.asyncio
async def test_server_close_reports_disconnect(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """The server hanging up is a connection error."""
    disconnects = Disconnects()
    await _connect(server, session, disconnects)

    assert server.ws is not None
    await server.ws.close()
    async with asyncio.timeout(2):
        await disconnects.event.wait()

    assert [type(error) for error in disconnects.errors] == [TionConnectionError]


@pytest.mark.asyncio
async def test_permissions_violation_keeps_connection(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """A refused subscription is not fatal."""
    disconnects = Disconnects()
    connection = await _connect(server, session, disconnects)
    server.received.clear()

    await server.send(b"-ERR 'Permissions Violation for Subscription to \"x\"'\r\n")
    await server.send(b"PING\r\n")
    await _until(server, b"PONG\r\n")

    assert disconnects.errors == []
    assert not connection.closed
    await connection.async_close()


@pytest.mark.asyncio
async def test_close_is_silent_and_final(
    server: FakeNatsServer, session: ClientSession
) -> None:
    """Closing does not report a disconnect; later publishes fail."""
    disconnects = Disconnects()
    connection = await _connect(server, session, disconnects)

    await connection.async_close()
    await asyncio.sleep(0.05)

    assert disconnects.errors == []
    with pytest.raises(TionConnectionError):
        await connection.async_publish("s", b"x")


@pytest.mark.parametrize(
    ("text", "error"),
    [
        pytest.param("Authorization Violation", TionAuthError, id="authorization"),
        pytest.param("authentication timeout", TionAuthError, id="authentication"),
        pytest.param("Maximum Connections Exceeded", TionConnectionError, id="other"),
    ],
)
def test_nats_error_classification(text: str, error: type[TionError]) -> None:
    """Authorization problems need a new login; the rest is transient."""
    assert type(nats_error(text)) is error
