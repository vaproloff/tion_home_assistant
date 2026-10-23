# ruff: noqa: INP001, TID251  # standalone script for users on Python 3.9+, not part of the integration
"""Forward 127.0.0.1:80 to a Home Assistant host over plain TCP.

Tion accepts the captcha token only from a page whose address is exactly
http://localhost. Home Assistant usually runs on another machine, so this
script makes port 80 of the computer running the browser lead to Home
Assistant's port (8123 by default). Keep it running while you sign in to Tion,
then stop it with Ctrl+C.

It forwards bytes as they are and works for Home Assistant served over plain
http. For an https address use captcha_proxy_https.py instead.

Usage: python3 captcha_proxy_local.py <HA-host> [port=8123] [--listen-port N]

Standard library only, Python 3.9+. Listens on 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

_LOGGER = logging.getLogger("captcha_proxy_local")

LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 80
DEFAULT_HA_PORT = 8123
BUFFER_SIZE = 65536
CONNECT_TIMEOUT = 10

_CLIENTS: set[asyncio.StreamWriter] = set()


def _port(value: str) -> int:
    """Parse a TCP port number for argparse."""
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise argparse.ArgumentTypeError(f"{value!r} is not a port (1-65535)")
    return int(value)


def _host(value: str) -> str:
    """Parse a bare host name or IP address for argparse."""
    if not value or "://" in value or "/" in value:
        raise argparse.ArgumentTypeError(
            f"{value!r} must be a host name or IP address without http:// or a path"
        )
    return value


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes from reader to writer until either side stops."""
    with contextlib.suppress(OSError):
        while data := await reader.read(BUFFER_SIZE):
            writer.write(data)
            await writer.drain()


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    host: str,
    port: int,
) -> None:
    """Connect one browser connection to Home Assistant and relay both ways."""
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), CONNECT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as err:  # noqa: UP041  # on 3.9 this is not the builtin TimeoutError
        _LOGGER.warning(
            "Cannot reach %s:%s: %s", host, port, str(err) or type(err).__name__
        )
        return

    _LOGGER.info(
        "Connection from %s relayed to %s:%s",
        client_writer.get_extra_info("peername"),
        host,
        port,
    )
    tasks = [
        asyncio.ensure_future(_pump(client_reader, upstream_writer)),
        asyncio.ensure_future(_pump(upstream_reader, client_writer)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        upstream_writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    host: str,
    port: int,
) -> None:
    """Serve one browser connection, tracked so that Ctrl+C can drop it."""
    _CLIENTS.add(client_writer)
    try:
        await _relay(client_reader, client_writer, host, port)
    finally:
        client_writer.close()
        _CLIENTS.discard(client_writer)


async def _serve(host: str, port: int, listen_port: int) -> int:
    """Run the forwarder, return the process exit code."""
    try:
        server = await asyncio.start_server(
            lambda reader, writer: _handle(reader, writer, host, port),
            LISTEN_HOST,
            listen_port,
        )
    except OSError as err:
        _LOGGER.error(
            "Cannot listen on %s:%s (%s). The port is probably in use by another "
            "program, or binding it needs elevated privileges (on Linux or macOS "
            "run the script with sudo; on Windows run: netstat -ano | findstr :80 "
            "(PID 4 means the built-in HTTP service holds the port)).",
            LISTEN_HOST,
            listen_port,
            err,
        )
        return 1

    _LOGGER.info(
        "Forwarding %s:%s to %s:%s. Press Ctrl+C to stop.",
        LISTEN_HOST,
        listen_port,
        host,
        port,
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
    """Parse the command line and run the forwarder until Ctrl+C."""
    parser = argparse.ArgumentParser(
        description="Forward 127.0.0.1:80 to Home Assistant over plain TCP."
    )
    parser.add_argument("host", type=_host, help="Home Assistant host name or IP")
    parser.add_argument(
        "port",
        nargs="?",
        type=_port,
        default=DEFAULT_HA_PORT,
        help=f"Home Assistant port (default {DEFAULT_HA_PORT})",
    )
    parser.add_argument(
        "--listen-port",
        type=_port,
        default=DEFAULT_LISTEN_PORT,
        help=(
            f"local port to listen on (default {DEFAULT_LISTEN_PORT}); Tion accepts "
            "the captcha only from port 80, other ports are for testing the "
            "forwarder only"
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(format="%(asctime)s %(message)s")
    _LOGGER.setLevel(logging.INFO)
    try:
        return asyncio.run(_serve(args.host, args.port, args.listen_port))
    except KeyboardInterrupt:
        _LOGGER.info("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
