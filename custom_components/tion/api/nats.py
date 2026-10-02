"""Minimal NATS client over WebSocket, as spoken by the Tion app."""

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
import itertools
import json
import logging
import ssl
from typing import Any

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

from .exceptions import TionAuthError, TionConnectionError, TionError

_LOGGER = logging.getLogger(__name__)

NATS_URL = "wss://hwgate-v4.magicair.tion.ru:443"
SUBPROTOCOL = "maprot"
CONNECT_TIMEOUT = 10.0
PING_INTERVAL = 30.0
PONG_TIMEOUT = 10.0
# The Tion app's client identity; the server sees no difference from it.
CLIENT_INFO = {"name": "mappV1.1.1225", "lang": "nats.ws", "version": "3.3.1"}

_CRLF = b"\r\n"

type MessageCallback = Callable[[str, bytes], None]
type DisconnectCallback = Callable[[TionError], None]
type TaskFactory = Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]


def _default_task_factory(
    coro: Coroutine[Any, Any, None], name: str
) -> asyncio.Task[None]:
    return asyncio.create_task(coro, name=name)


def nats_error(text: str) -> TionError:
    """Classify a server -ERR: authorization problems need a new login."""
    lowered = text.lower()
    if "authorization" in lowered or "authentication" in lowered:
        return TionAuthError(f"NATS: {text}")
    return TionConnectionError(f"NATS: {text}")


class NatsConnection:
    """One authenticated NATS connection: subscribe, publish, keepalive."""

    def __init__(
        self,
        ws: ClientWebSocketResponse,
        on_disconnect: DisconnectCallback,
        create_task: TaskFactory,
        ping_interval: float,
        pong_timeout: float,
    ) -> None:
        """Wrap an open WebSocket; use async_connect instead."""
        self._ws = ws
        self._on_disconnect = on_disconnect
        self._create_task = create_task
        self._ping_interval = ping_interval
        self._pong_timeout = pong_timeout
        self._buffer = bytearray()
        self._subscriptions: dict[str, MessageCallback] = {}
        self._sids = itertools.count(1)
        self._send_lock = asyncio.Lock()
        self._pongs: list[asyncio.Future[None]] = []
        self._handshake_error: TionError | None = None
        self._closed = False
        self._tasks: list[asyncio.Task[None]] = []

    @classmethod
    async def async_connect(
        cls,
        session: ClientSession,
        *,
        user: str,
        auth_token: str,
        on_disconnect: DisconnectCallback,
        ssl_context: ssl.SSLContext | bool = True,
        url: str = NATS_URL,
        create_task: TaskFactory | None = None,
        connect_timeout: float = CONNECT_TIMEOUT,
        ping_interval: float = PING_INTERVAL,
        pong_timeout: float = PONG_TIMEOUT,
    ) -> NatsConnection:
        """Open the WebSocket, authenticate and start reading."""
        try:
            async with asyncio.timeout(connect_timeout):
                ws = await session.ws_connect(
                    url, protocols=(SUBPROTOCOL,), ssl=ssl_context
                )
        except (ClientError, TimeoutError) as err:
            raise TionConnectionError(f"NATS connect: {err!r}") from err
        connection = cls(
            ws,
            on_disconnect,
            create_task or _default_task_factory,
            ping_interval,
            pong_timeout,
        )
        try:
            async with asyncio.timeout(connect_timeout):
                await connection._handshake(user, auth_token)
        except TimeoutError as err:
            await ws.close()
            raise TionConnectionError("NATS handshake timed out") from err
        except TionError:
            await ws.close()
            raise
        connection._start()
        return connection

    async def _handshake(self, user: str, auth_token: str) -> None:
        if not (await self._receive()).startswith(b"INFO"):
            raise TionConnectionError("NATS server did not send INFO")
        options = {
            "no_responders": True,
            "protocol": 1,
            "verbose": False,
            "pedantic": False,
            "user": user,
            "auth_token": auth_token,
            **CLIENT_INFO,
            "headers": True,
        }
        pong = asyncio.get_running_loop().create_future()
        self._pongs.append(pong)
        await self._send(
            b"CONNECT " + json.dumps(options).encode() + _CRLF + b"PING\r\n"
        )
        while not pong.done():
            self._feed(await self._receive())
            if self._handshake_error is not None:
                raise self._handshake_error

    async def _receive(self) -> bytes:
        message = await self._ws.receive()
        if message.type is WSMsgType.BINARY:
            return message.data
        if message.type is WSMsgType.TEXT:
            return message.data.encode()
        raise TionConnectionError(f"NATS connection closed ({message.type.name})")

    def _start(self) -> None:
        self._tasks = [
            self._create_task(self._read_loop(), "tion_nats_reader"),
            self._create_task(self._ping_loop(), "tion_nats_keepalive"),
        ]

    @property
    def closed(self) -> bool:
        """Return True once the connection is closed or lost."""
        return self._closed

    async def async_subscribe(self, subject: str, callback: MessageCallback) -> str:
        """Subscribe to a subject; returns the subscription id."""
        sid = str(next(self._sids))
        self._subscriptions[sid] = callback
        await self._send(f"SUB {subject} {sid}".encode() + _CRLF)
        return sid

    async def async_unsubscribe(self, sid: str) -> None:
        """Drop a subscription."""
        if self._subscriptions.pop(sid, None) is not None:
            await self._send(f"UNSUB {sid}".encode() + _CRLF)

    async def async_publish(self, subject: str, payload: bytes) -> None:
        """Publish a message."""
        header = f"PUB {subject} {len(payload)}".encode()
        await self._send(header + _CRLF + payload + _CRLF)

    async def async_close(self) -> None:
        """Close the connection without reporting a disconnect."""
        self._closed = True
        await self._shutdown()

    async def _send_op(self, op: str) -> None:
        with contextlib.suppress(TionConnectionError):
            await self._send(op.encode() + _CRLF)

    async def _send(self, data: bytes) -> None:
        if self._closed:
            raise TionConnectionError("NATS connection is closed")
        try:
            async with self._send_lock:
                await self._ws.send_bytes(data)
        except (ClientError, ConnectionError, RuntimeError) as err:
            raise TionConnectionError(f"NATS send: {err!r}") from err

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                self._feed(await self._receive())
        except TionError as err:
            self._lost(err)

    async def _ping_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._ping_interval)
            pong = asyncio.get_running_loop().create_future()
            self._pongs.append(pong)
            try:
                await self._send(b"PING\r\n")
                async with asyncio.timeout(self._pong_timeout):
                    await pong
            except TimeoutError:
                self._lost(TionConnectionError("NATS keepalive timed out"))
            except TionError as err:
                self._lost(err)

    def _lost(self, error: TionError) -> None:
        if self._closed:
            return
        self._closed = True
        _LOGGER.debug("NATS connection lost: %s", error)
        self._create_task(self._shutdown(), "tion_nats_shutdown")
        self._on_disconnect(error)

    async def _shutdown(self) -> None:
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        for pong in self._pongs:
            pong.cancel()
        self._pongs.clear()
        await self._ws.close()

    def _feed(self, data: bytes) -> None:
        self._buffer += data
        while (end := self._buffer.find(_CRLF)) >= 0:
            line = bytes(self._buffer[:end])
            op, _, args = line.partition(b" ")
            op = op.upper()
            if op in (b"MSG", b"HMSG"):
                if not self._take_message(op, args.split(), end + len(_CRLF)):
                    return
                continue
            del self._buffer[: end + len(_CRLF)]
            self._control(op, args)

    def _take_message(self, op: bytes, args: list[bytes], start: int) -> bool:
        """Consume one MSG/HMSG; False if its payload has not arrived yet."""
        total = int(args[-1])
        header_length = int(args[-2]) if op == b"HMSG" else 0
        if len(self._buffer) < start + total + len(_CRLF):
            return False
        body = bytes(self._buffer[start + header_length : start + total])
        del self._buffer[: start + total + len(_CRLF)]
        subject, sid = args[0].decode(), args[1].decode()
        if (callback := self._subscriptions.get(sid)) is not None:
            try:
                callback(subject, body)
            except Exception:
                _LOGGER.exception("Error handling NATS message on %s", subject)
        return True

    def _control(self, op: bytes, args: bytes) -> None:
        if op == b"PING":
            self._create_task(self._send_op("PONG"), "tion_nats_pong")
        elif op == b"PONG":
            while self._pongs:
                if not (pong := self._pongs.pop(0)).done():
                    pong.set_result(None)
                    break
        elif op == b"-ERR":
            text = args.decode(errors="replace").strip().strip("'")
            if "permissions violation" in text.lower():
                _LOGGER.warning("NATS: %s", text)
                return
            error = nats_error(text)
            if self._tasks:
                self._lost(error)
            else:
                self._handshake_error = error
