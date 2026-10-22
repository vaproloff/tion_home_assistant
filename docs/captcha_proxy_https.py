# ruff: noqa: INP001, TID251  # standalone script for users on Python 3.9+, not part of the integration
"""Serve a Home Assistant behind an HTTPS address at http://localhost.

Tion accepts the captcha token only from a page whose address is exactly
http://localhost. When Home Assistant is reached through its own HTTPS address
(for example a Keenetic cloud domain), a plain TCP forward cannot work: the TLS
endpoint expects its own host name. This script is a small HTTP/1.1 proxy that
listens on 127.0.0.1:80, passes every request to the given address with the
Host header of that address and returns the answer to the browser. Keep it
running while you sign in to Tion, then stop it with Ctrl+C.

Requests are handled one at a time per connection without keep-alive, request
bodies are read by Content-Length (chunked requests get 411), WebSocket
upgrades are not supported. This is enough for the captcha page.

Usage: python3 captcha_proxy_https.py <https://host[:port] | http://host[:port]>
                                      [--listen-port N]

Standard library only, Python 3.9+. Listens on 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import ssl
import sys
from typing import NamedTuple
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("captcha_proxy_https")

LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 80
BUFFER_SIZE = 65536
HEADER_LIMIT = 262144
CONNECT_TIMEOUT = 15

_CLIENTS: set[asyncio.StreamWriter] = set()

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "upgrade",
        "te",
        "trailer",
    }
)
# The response body is relayed byte for byte, so its framing header must stay.
REQUEST_DROP = HOP_BY_HOP | {"host"}
RESPONSE_DROP = HOP_BY_HOP - {"transfer-encoding"}

Headers = list[tuple[str, str]]


class Target(NamedTuple):
    """Where requests are forwarded to."""

    host: str
    port: int
    host_header: str
    ssl_context: ssl.SSLContext | None


def _port(value: str) -> int:
    """Parse a TCP port number for argparse."""
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise argparse.ArgumentTypeError(f"{value!r} is not a port (1-65535)")
    return int(value)


def _target(value: str) -> Target:
    """Parse scheme://host[:port] for argparse."""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise argparse.ArgumentTypeError(
            f"{value!r} must look like https://host[:port] or http://host[:port]"
        )
    if (
        "@" in parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise argparse.ArgumentTypeError(
            f"{value!r} must be only scheme, host and optional port, without a path"
        )
    try:
        explicit_port = parts.port
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} has an invalid port") from None

    is_https = parts.scheme == "https"
    host = parts.hostname
    host_header = f"[{host}]" if ":" in host else host
    if explicit_port is not None:
        host_header = f"{host_header}:{explicit_port}"
    return Target(
        host,
        explicit_port or (443 if is_https else 80),
        host_header,
        ssl.create_default_context() if is_https else None,
    )


def _parse_head(raw: bytes) -> tuple[str, Headers]:
    """Split a message head into its first line and header pairs."""
    first, *lines = raw.decode("latin-1").split("\r\n")
    headers: Headers = []
    for line in lines:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip():
            raise ValueError(f"malformed header line {line!r}")
        headers.append((name.strip(), value.strip()))
    return first, headers


def _build_head(first: str, headers: Headers, *extra: str) -> bytes:
    """Serialize a message head."""
    lines = [first, *(f"{name}: {value}" for name, value in headers), *extra, "", ""]
    return "\r\n".join(lines).encode("latin-1")


async def _send_error(
    writer: asyncio.StreamWriter, status: int, reason: str, detail: str
) -> None:
    """Answer the browser with a plain text error and nothing else."""
    _LOGGER.warning("%s %s: %s", status, reason, detail)
    body = f"{status} {reason}: {detail}\n".encode()
    writer.write(
        _build_head(
            f"HTTP/1.1 {status} {reason}",
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ],
        )
        + body
    )
    await writer.drain()


async def _read_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> tuple[str, str, Headers, bytes] | None:
    """Read one request, None if the browser got an error answer or left."""
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        # Browsers open spare connections and close them unused.
        return None
    except asyncio.LimitOverrunError:
        await _send_error(
            writer, 431, "Request Header Fields Too Large", "headers are too long"
        )
        return None

    try:
        first, headers = _parse_head(raw)
        method, path, _version = first.split(" ", 2)
    except ValueError:
        await _send_error(writer, 400, "Bad Request", "malformed request")
        return None

    names = {name.lower(): value for name, value in headers}
    if "chunked" in names.get("transfer-encoding", "").lower():
        await _send_error(
            writer, 411, "Length Required", "chunked requests are not supported"
        )
        return None

    length = names.get("content-length", "0")
    if not (length.isascii() and length.isdigit()):
        await _send_error(writer, 400, "Bad Request", "invalid Content-Length")
        return None
    return method, path, headers, await reader.readexactly(int(length))


async def _read_response_head(
    reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, target: Target
) -> tuple[str, Headers] | None:
    """Read the target's status line and headers, None after a 502 to the browser."""
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
        return _parse_head(raw)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as err:
        await _send_error(
            client_writer,
            502,
            "Bad Gateway",
            f"{target.host_header} sent an incomplete or malformed answer ({type(err).__name__})",
        )
        return None


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target: Target,
) -> None:
    """Pass one request to the target and its answer back to the browser."""
    request = await _read_request(client_reader, client_writer)
    if request is None:
        return
    method, path, headers, body = request

    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(
                target.host, target.port, ssl=target.ssl_context, limit=HEADER_LIMIT
            ),
            CONNECT_TIMEOUT,
        )
    except (OSError, asyncio.TimeoutError) as err:  # noqa: UP041  # on 3.9 this is not the builtin TimeoutError
        await _send_error(
            client_writer,
            502,
            "Bad Gateway",
            f"cannot connect to {target.host_header}: {str(err) or type(err).__name__}",
        )
        return

    try:
        upstream_writer.write(
            _build_head(
                f"{method} {path} HTTP/1.1",
                [h for h in headers if h[0].lower() not in REQUEST_DROP],
                f"Host: {target.host_header}",
                "Connection: close",
            )
            + body
        )
        await upstream_writer.drain()
        head = await _read_response_head(upstream_reader, client_writer, target)
        if head is None:
            return
        status_line, response_headers = head

        _LOGGER.info("%s %s -> %s", method, path, status_line)
        client_writer.write(
            _build_head(
                status_line,
                [h for h in response_headers if h[0].lower() not in RESPONSE_DROP],
                "Connection: close",
            )
        )
        while data := await upstream_reader.read(BUFFER_SIZE):
            client_writer.write(data)
            await client_writer.drain()
    finally:
        upstream_writer.close()


async def _handle(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: Target
) -> None:
    """Serve one browser connection, tracked so that Ctrl+C can drop it."""
    _CLIENTS.add(writer)
    try:
        await _relay(reader, writer, target)
    except (OSError, asyncio.IncompleteReadError) as err:
        _LOGGER.debug("Connection ended early: %r", err)
    finally:
        writer.close()
        _CLIENTS.discard(writer)


async def _serve(target: Target, listen_port: int) -> int:
    """Run the proxy, return the process exit code."""
    try:
        server = await asyncio.start_server(
            lambda reader, writer: _handle(reader, writer, target),
            LISTEN_HOST,
            listen_port,
            limit=HEADER_LIMIT,
        )
    except OSError as err:
        _LOGGER.error(
            "Cannot listen on %s:%s (%s). The port is probably in use by another "
            "program, or binding it needs elevated privileges (on Linux or macOS "
            "run the script with sudo).",
            LISTEN_HOST,
            listen_port,
            err,
        )
        return 1

    _LOGGER.info(
        "Proxying %s:%s to %s (Host: %s). Press Ctrl+C to stop.",
        LISTEN_HOST,
        listen_port,
        "https" if target.ssl_context else "http",
        target.host_header,
    )
    try:
        await asyncio.Event().wait()
    finally:
        # Not serve_forever() or "async with server": since Python 3.12.1 they
        # wait for every open browser connection, so one Ctrl+C would do nothing.
        server.close()
        for writer in list(_CLIENTS):
            writer.transport.abort()
    return 0


def main() -> int:
    """Parse the command line and run the proxy until Ctrl+C."""
    parser = argparse.ArgumentParser(
        description="Serve a Home Assistant behind an HTTPS address at http://localhost."
    )
    parser.add_argument(
        "target",
        type=_target,
        help="Home Assistant address: https://host[:port] or http://host[:port]",
    )
    parser.add_argument(
        "--listen-port",
        type=_port,
        default=DEFAULT_LISTEN_PORT,
        help=f"local port to listen on (default {DEFAULT_LISTEN_PORT})",
    )
    args = parser.parse_args()

    logging.basicConfig(format="%(asctime)s %(message)s")
    _LOGGER.setLevel(logging.INFO)
    try:
        return asyncio.run(_serve(args.target, args.listen_port))
    except KeyboardInterrupt:
        _LOGGER.info("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
