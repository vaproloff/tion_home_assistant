"""Fake gRPC, session and NATS for TionCloud, with simulated devices."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from custom_components.tion.api.auth import TionTokens
from custom_components.tion.api.datapoints import DPKind, DPValue, decode_dp_value
from custom_components.tion.api.device_key import TionDeviceKey
from custom_components.tion.api.exceptions import TionConnectionError, TionError
from custom_components.tion.api.protobuf import ProtoMessage, encode_varint

from .payloads import (  # noqa: TID251
    PROFILE_4S,
    PROFILE_BS310,
    PROFILE_CLEVER,
    auto_control,
    device,
    location,
    room,
    state_report,
    structure_response,
    update_response,
)

PROFILES = (Path(__file__).parent / "fixtures" / "device_profiles.bin").read_bytes()
SID = "LOC0000001"
BREEZER = "BRZ0000001"
STATION = "MAG0000001"
SERVER_TIME = 1_790_865_786.0
SECOND_LOCATION_ID = UUID(int=9)
SECOND_ROOM_ID = UUID(int=10)
DEVICE_KEY = TionDeviceKey.generate()


def cloud_structure(*, wstoken: str = "ws-1", extra_location: bool = False) -> bytes:
    """Return a structure with a 4S behind a BS310, and an unsupported device."""
    home = location(
        SID,
        rooms=(
            room(
                auto=auto_control(
                    enabled=False, speed_min=1, speed_max=5, co2_target=800
                )
            ),
        ),
        devices=(
            device(BREEZER, PROFILE_4S, parent_id=STATION),
            device(STATION, PROFILE_BS310, is_gateway=True),
            device("CLV0000001", PROFILE_CLEVER),
        ),
    )
    locations = [home]
    if extra_location:
        locations.append(
            location(
                "LOC0000002",
                location_id=SECOND_LOCATION_ID,
                rooms=(
                    room(
                        SECOND_ROOM_ID,
                        auto=auto_control(
                            enabled=False, speed_min=1, speed_max=5, co2_target=800
                        ),
                    ),
                ),
                devices=(device("BRZ0000002", PROFILE_4S, room_id=None),),
            )
        )
    return structure_response(*locations, wstoken=wstoken)


class FakeTransport:
    """gRPC calls answered from per-method queues (the last answer repeats)."""

    def __init__(self) -> None:
        """Answer profiles and structure."""
        self.session = object()
        self.ssl_context = object()
        self.calls: list[tuple[str, str | None]] = []
        self.payloads: list[tuple[str, bytes]] = []
        self.location_ids: list[tuple[str, UUID | None]] = []
        self.answers: dict[str, list[bytes | Exception]] = {
            "GetDeviceProfiles": [PROFILES],
            "GetFullStructureLocations": [cloud_structure()],
            "SetAutoControlParams": [encode_varint(1, 1)],
        }

    async def async_call(
        self,
        service: str,
        method: str,
        payload: bytes,
        token: str | None = None,
        *,
        location_id: UUID | None = None,
    ) -> bytes:
        """Record the call and answer it, yielding like real I/O."""
        self.calls.append((method, token))
        self.location_ids.append((method, location_id))
        self.payloads.append((method, payload))
        await asyncio.sleep(0)
        queue = self.answers[method]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def server_time(self) -> float:
        """Return a fixed server clock."""
        return SERVER_TIME

    def methods(self) -> list[str]:
        """Return the called methods in order."""
        return [method for method, _ in self.calls]


class FakeAuth:
    """A session whose renewals hand out access-2, access-3, ..."""

    def __init__(self) -> None:
        """Start with access-1."""
        self.token = "access-1"
        self.renewals = 0
        self.renew_error: TionError | None = None
        self.device_key = DEVICE_KEY

    async def async_ensure_valid(self) -> str:
        """Return the current token."""
        return self.token

    async def async_renew_access(self) -> TionTokens:
        """Renew, unless told to fail."""
        if self.renew_error is not None:
            raise self.renew_error
        self.renewals += 1
        self.token = f"access-{self.renewals + 1}"
        return TionTokens(self.token, "renew", SERVER_TIME + 900, SERVER_TIME + 86400)


def _matches(pattern: str, subject: str) -> bool:
    pattern_parts, subject_parts = pattern.split("."), subject.split(".")
    for index, part in enumerate(pattern_parts):
        if part == ">":
            return len(subject_parts) > index
        if index >= len(subject_parts) or part not in ("*", subject_parts[index]):
            return False
    return len(pattern_parts) == len(subject_parts)


class FakeNats:
    """One fake NATS connection routed through the broker."""

    def __init__(self, broker: FakeBroker, kwargs: dict[str, Any]) -> None:
        """Remember how the cloud connected."""
        self.broker = broker
        self.kwargs = kwargs
        self.subscriptions: dict[str, tuple[str, Callable[[str, bytes], None]]] = {}
        self.published: list[tuple[str, bytes]] = []
        self.closed = False
        self.close_calls = 0

    async def async_subscribe(
        self, subject: str, callback: Callable[[str, bytes], None]
    ) -> str:
        """Register a subscription, yielding like real I/O."""
        await asyncio.sleep(0)
        sid = str(len(self.subscriptions) + len(self.published) + 1)
        while sid in self.subscriptions:
            sid += "x"
        self.subscriptions[sid] = (subject, callback)
        return sid

    async def async_unsubscribe(self, sid: str) -> None:
        """Drop a subscription, yielding like real I/O."""
        await asyncio.sleep(0)
        self.subscriptions.pop(sid)

    async def async_publish(self, subject: str, payload: bytes) -> None:
        """Hand the message to the simulated devices."""
        if self.closed:
            raise TionConnectionError("closed")
        self.published.append((subject, payload))
        self.broker.on_publish(self, subject, payload)

    async def async_close(self) -> None:
        """Close silently."""
        self.closed = True
        self.close_calls += 1

    def subjects(self) -> list[str]:
        """Return the subscribed subjects."""
        return sorted(subject for subject, _ in self.subscriptions.values())

    def deliver(self, subject: str, payload: bytes) -> None:
        """Deliver a message to matching subscriptions."""
        for pattern, callback in list(self.subscriptions.values()):
            if _matches(pattern, subject):
                callback(subject, payload)

    def drop(self, error: TionError) -> None:
        """Simulate the server dropping the connection."""
        self.closed = True
        self.kwargs["on_disconnect"](error)


class FakeBroker:
    """Simulated devices answering queries and commands."""

    def __init__(self) -> None:
        """Devices report a few recorded values."""
        self.connections: list[FakeNats] = []
        self.connect_errors: list[Exception] = []
        self.state: dict[str, tuple[DPValue, ...]] = {
            BREEZER: (
                DPValue(70, DPKind.BOOL, True),
                DPValue(130, DPKind.INT, 150),
                DPValue(140, DPKind.INT, 1),
            ),
            STATION: (DPValue(100, DPKind.INT, 244), DPValue(113, DPKind.INT, 405)),
        }
        self.silent: set[str] = set()
        self.command_error: tuple[int, str] | None = None
        self.answer_commands = True
        self.logins_lost = 0

    async def connect(self, session: object, **kwargs: Any) -> FakeNats:
        """Open a connection, or fail as scripted."""
        if self.connect_errors:
            raise self.connect_errors.pop(0)
        connection = FakeNats(self, {"session": session, **kwargs})
        self.connections.append(connection)
        if self.logins_lost:
            # An eager reader can see the loss before the connect returns.
            self.logins_lost -= 1
            connection.drop(TionConnectionError("lost after login"))
        return connection

    @property
    def last(self) -> FakeNats:
        """Return the newest connection."""
        return self.connections[-1]

    def on_publish(self, connection: FakeNats, subject: str, payload: bytes) -> None:
        """Answer a query or a command like a device would."""
        _, _, sid, service, device_id = subject.split(".")
        message = ProtoMessage.parse(payload)
        if service == "dps" and device_id not in self.silent:
            connection.deliver(
                f"hw.tx.{sid}.dps.{device_id}",
                state_report(
                    device_id,
                    *self.state.get(device_id, ()),
                    command_id=message.get_int(4),
                ),
            )
        elif service == "dpu" and self.answer_commands:
            command_id = message.get_int(2)
            if self.command_error is not None:
                code, text = self.command_error
                answer = update_response(
                    command_id, success=False, error_code=code, error_message=text
                )
            else:
                values = [decode_dp_value(item) for item in message.get_messages(4)]
                answer = update_response(command_id, *values)
            connection.deliver(f"hw.tx.{sid}.dpu.{device_id}", answer)
