"""Tests for TionCloud on fake gRPC, session and NATS."""

import asyncio
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Any
from uuid import UUID

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer
import pytest
import pytest_asyncio

from custom_components.tion.api import cloud as cloud_module
from custom_components.tion.api.cloud import TionCloud
from custom_components.tion.api.datapoints import DPKind, DPValue, encode_update_request
from custom_components.tion.api.exceptions import (
    TionApiError,
    TionAuthError,
    TionCommandError,
    TionConnectionError,
)
from custom_components.tion.api.model import AutoControl
from custom_components.tion.api.nats import NatsConnection, TaskFactory
from custom_components.tion.api.protobuf import ProtoMessage, encode_varint
from custom_components.tion.api.structure import encode_set_auto_control
from custom_components.tion.api.views import Breezer, DeviceCommand, Station, view

from .fakes import (  # noqa: TID251
    BREEZER,
    DEVICE_KEY,
    PROFILES,
    SECOND_LOCATION_ID,
    SECOND_ROOM_ID,
    SERVER_TIME,
    SID,
    STATION,
    FakeAuth,
    FakeBroker,
    FakeNats,
    FakeTransport,
    cloud_structure,
)
from .payloads import (  # noqa: TID251
    LOCATION_ID,
    ROOM_ID,
    auto_control,
    auto_control_changed,
    device,
    error_response,
    location,
    room,
    state_report,
    structure_response,
    update_response,
)


class Harness:
    """A TionCloud wired to fakes, with recorded sleeps and notifications."""

    def __init__(self, create_task: TaskFactory | None) -> None:
        """Build the fakes and the cloud."""
        self.transport = FakeTransport()
        self.auth = FakeAuth()
        self.broker = FakeBroker()
        self.sleeps: list[float] = []
        self.notifications = 0
        self.cloud = TionCloud(
            self.transport,
            self.auth,
            create_task=create_task,
            connect=self.broker.connect,
            sleep=self.sleep,
        )
        self.cloud.add_listener(self.listener)

    async def sleep(self, delay: float) -> None:
        """Record the delay and yield once."""
        self.sleeps.append(delay)
        await asyncio.sleep(0)

    def listener(self) -> None:
        """Count snapshot changes."""
        self.notifications += 1

    def breezer(self) -> Breezer:
        """Return the view of the 4S."""
        found = self.cloud.account.device(BREEZER)
        assert found is not None
        device_view = view(found)
        assert isinstance(device_view, Breezer)
        return device_view

    def station(self) -> Station:
        """Return the view of the MagicAir."""
        found = self.cloud.account.device(STATION)
        assert found is not None
        device_view = view(found)
        assert isinstance(device_view, Station)
        return device_view


async def _eventually(predicate: Callable[[], bool]) -> None:
    """Let background tasks run until the predicate holds."""
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


def _drop_after_last_reply(harness: Harness) -> None:
    """Once, let the server drop the channel right after the last poll reply."""
    original = harness.broker.on_publish

    def on_publish(connection: FakeNats, subject: str, payload: bytes) -> None:
        original(connection, subject, payload)
        if subject == f"hw.rx.{SID}.dps.{STATION}":
            harness.broker.on_publish = original
            connection.drop(TionConnectionError("gone"))

    harness.broker.on_publish = on_publish


def _fail_publish(harness: Harness) -> None:
    """Make the next publish fail without the connection reporting a loss."""
    original = harness.broker.on_publish

    def on_publish(connection: FakeNats, subject: str, payload: bytes) -> None:
        harness.broker.on_publish = original
        raise TionConnectionError("send failed")

    harness.broker.on_publish = on_publish


BROKEN_POLLS = [
    pytest.param(_drop_after_last_reply, id="dropped_after_last_reply"),
    pytest.param(_fail_publish, id="publish_failed"),
]


@pytest.fixture
def harness(create_task: TaskFactory | None) -> Harness:
    """A fresh harness, with a lazy or an eager task factory."""
    return Harness(create_task)


@pytest.fixture
def short_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make unanswered queries and commands time out quickly."""
    monkeypatch.setattr(cloud_module, "REPLY_TIMEOUT", 0.05)


@pytest.mark.asyncio
async def test_start(harness: Harness) -> None:
    """Start loads profiles and structure, connects, subscribes and polls."""
    await harness.cloud.async_start()

    assert harness.transport.calls == [
        ("GetDeviceProfiles", "access-1"),
        ("GetFullStructureLocations", "access-1"),
    ]
    connection = harness.broker.last
    assert connection.kwargs["session"] is harness.transport.session
    assert connection.kwargs["ssl_context"] is harness.transport.ssl_context
    assert connection.kwargs["user"] == "mapp" + DEVICE_KEY.key_id()
    assert connection.kwargs["auth_token"] == "access-1:ws-1"
    assert connection.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]
    assert sorted(subject for subject, _ in connection.published) == [
        f"hw.rx.{SID}.dps.{BREEZER}",
        f"hw.rx.{SID}.dps.{STATION}",
    ]
    assert harness.cloud.connected
    assert harness.cloud.account.connected
    breezer = harness.breezer()
    assert (breezer.is_on, breezer.speed, breezer.target_temperature) == (
        True,
        1,
        15.0,
    )
    assert (harness.station().co2, harness.station().temperature) == (405, 24.4)
    assert harness.notifications > 0


@pytest.mark.asyncio
async def test_query_asks_for_view_datapoints(harness: Harness) -> None:
    """A device is polled for its view's datapoints."""
    await harness.cloud.async_start()

    payload = dict(harness.broker.last.published)[f"hw.rx.{SID}.dps.{STATION}"]
    assert ProtoMessage.parse(payload).get_packed(3) == [10, 76, 100, 110, 113]


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_replies")
async def test_silent_device_is_not_an_error(harness: Harness) -> None:
    """A device that does not answer keeps unknown values."""
    harness.broker.silent.add(BREEZER)

    await harness.cloud.async_start()

    assert harness.breezer().speed is None
    assert harness.station().co2 == 405


@pytest.mark.asyncio
async def test_push_applies_in_arrival_order(harness: Harness) -> None:
    """Pushed reports update the snapshot and notify listeners."""
    await harness.cloud.async_start()
    before = harness.notifications

    for speed in (2, 4):
        harness.broker.last.deliver(
            f"hw.tx.{SID}.dps.{BREEZER}",
            state_report(BREEZER, DPValue(140, DPKind.INT, speed)),
        )

    assert harness.breezer().speed == 4
    assert harness.notifications == before + 2


@pytest.mark.parametrize(
    ("success", "speed"),
    [pytest.param(True, 3, id="success"), pytest.param(False, 1, id="refused")],
)
@pytest.mark.asyncio
async def test_foreign_command_results(
    harness: Harness, success: bool, speed: int
) -> None:
    """Results of the app's commands update the snapshot only on success."""
    await harness.cloud.async_start()

    harness.broker.last.deliver(
        f"hw.tx.{SID}.dpu.{BREEZER}",
        update_response(777, DPValue(140, DPKind.INT, 3), success=success),
    )

    assert harness.breezer().speed == speed


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param(f"hw.tx.{SID}.dps.{BREEZER}", id="malformed_report"),
        pytest.param(f"hw.tx.{SID}.evt.{BREEZER}", id="event"),
        pytest.param(f"app.location.{SID}.{'AutoControlChanged'}", id="bad_event"),
    ],
)
@pytest.mark.asyncio
async def test_bad_or_ignored_messages(harness: Harness, subject: str) -> None:
    """Malformed and uninteresting messages are skipped."""
    await harness.cloud.async_start()
    before = harness.cloud.account

    harness.broker.last.deliver(subject, b"\xff")

    assert harness.cloud.account is before
    assert harness.cloud.connected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            auto_control_changed(
                ROOM_ID, enabled=True, speed_min=2, speed_max=4, co2_target=700
            ),
            AutoControl(True, 2, 4, 700, 1),
            id="changed",
        ),
        pytest.param(
            auto_control_changed(ROOM_ID, enabled=True, removed=True),
            None,
            id="removed",
        ),
        pytest.param(
            auto_control_changed(ROOM_ID, enabled=True),
            AutoControl(True, 1, 5, 800, 1),
            id="switch_only",
        ),
    ],
)
@pytest.mark.asyncio
async def test_auto_control_changed_event(
    harness: Harness, payload: bytes, expected: AutoControl | None
) -> None:
    """Room auto mode follows AutoControlChanged events."""
    await harness.cloud.async_start()

    harness.broker.last.deliver(f"app.location.{SID}.AutoControlChanged", payload)

    room_info = harness.cloud.account.room(ROOM_ID)
    assert room_info is not None
    assert room_info.auto == expected


@pytest.mark.asyncio
async def test_location_event_refreshes_structure_once(harness: Harness) -> None:
    """Other location events trigger one delayed structure refresh."""
    await harness.cloud.async_start()
    harness.transport.calls.clear()

    harness.broker.last.deliver(f"app.location.{SID}.DeviceRegistered", b"")
    harness.broker.last.deliver(f"app.location.{SID}.DeviceRenamed", b"")
    await _eventually(
        lambda: "GetFullStructureLocations" in harness.transport.methods()
    )
    await asyncio.sleep(0)

    assert harness.sleeps == [cloud_module.STRUCTURE_REFRESH_DELAY]
    assert harness.transport.methods() == ["GetFullStructureLocations"]


@pytest.mark.asyncio
async def test_refresh_syncs_locations(harness: Harness) -> None:
    """Refresh subscribes to new locations, drops vanished ones and polls."""
    await harness.cloud.async_start()
    connection = harness.broker.last
    harness.transport.answers["GetFullStructureLocations"] = [
        cloud_structure(wstoken="ws-2", extra_location=True)
    ]
    connection.published.clear()

    await harness.cloud.async_refresh()

    assert connection.subjects() == [
        f"app.location.{SID}.*",
        "app.location.LOC0000002.*",
        f"hw.tx.{SID}.>",
        "hw.tx.LOC0000002.>",
    ]
    assert "hw.rx.LOC0000002.dps.BRZ0000002" in dict(connection.published)
    assert harness.cloud.account.device("BRZ0000002") is not None

    harness.transport.answers["GetFullStructureLocations"] = [cloud_structure()]
    await harness.cloud.async_refresh()

    assert connection.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]


THREE_LOCATIONS = structure_response(
    location(SID),
    location("LOC0000002", location_id=UUID(int=2)),
    location("LOC0000003", location_id=UUID(int=3)),
)


@pytest.mark.asyncio
async def test_concurrent_refreshes_drop_vanished_locations_once(
    harness: Harness,
) -> None:
    """Overlapping refreshes unsubscribe every vanished location exactly once."""
    harness.transport.answers["GetFullStructureLocations"] = [THREE_LOCATIONS]
    await harness.cloud.async_start()
    connection = harness.broker.last
    harness.transport.answers["GetFullStructureLocations"] = [cloud_structure()]

    await asyncio.gather(harness.cloud.async_refresh(), harness.cloud.async_refresh())

    assert connection.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]


@pytest.mark.asyncio
async def test_drop_during_subscription_sync(harness: Harness) -> None:
    """A drop while a refresh drops vanished locations breaks neither of them."""
    harness.transport.answers["GetFullStructureLocations"] = [THREE_LOCATIONS]
    await harness.cloud.async_start()
    connection = harness.broker.last
    harness.transport.answers["GetFullStructureLocations"] = [cloud_structure()]

    refresh = asyncio.create_task(harness.cloud.async_refresh())
    # Drop once the first of the four vanished subscriptions is gone.
    await _eventually(lambda: len(connection.subscriptions) == 5)
    connection.drop(TionConnectionError("gone"))
    await refresh
    await _eventually(lambda: harness.cloud.connected)

    assert harness.broker.last.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]


@pytest.mark.asyncio
async def test_refresh_during_reconnect_subscribes_once(harness: Harness) -> None:
    """A refresh overlapping a reconnect does not subscribe a location twice."""
    await harness.cloud.async_start()
    harness.broker.last.drop(TionConnectionError("gone"))
    # The reconnect is subscribing on the new connection now.
    await _eventually(lambda: len(harness.broker.connections) == 2)

    await harness.cloud.async_refresh()
    await _eventually(lambda: harness.cloud.connected)

    assert harness.broker.last.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]


@pytest.mark.asyncio
async def test_refresh_while_disconnected_readscloud_structure(
    harness: Harness,
) -> None:
    """Without the live channel, refresh only re-reads the structure."""
    await harness.cloud.async_start()
    await harness.cloud.async_stop()
    harness.transport.calls.clear()

    await harness.cloud.async_refresh()

    assert harness.transport.methods() == ["GetFullStructureLocations"]


@pytest.mark.asyncio
async def test_refresh_errors_propagate(harness: Harness) -> None:
    """A failed structure read during refresh is raised."""
    await harness.cloud.async_start()
    harness.transport.answers["GetFullStructureLocations"] = [error_response(2, "x")]

    with pytest.raises(TionApiError):
        await harness.cloud.async_refresh()


@pytest.mark.asyncio
async def test_unknown_profile_reloads_catalog(harness: Harness) -> None:
    """A device with an unknown profile triggers one catalog reload."""
    unknown = structure_response(
        location(SID, devices=(device("NEW0000001", UUID(int=77)),))
    )
    harness.transport.answers["GetFullStructureLocations"] = [unknown]

    await harness.cloud.async_start()

    assert harness.transport.methods() == [
        "GetDeviceProfiles",
        "GetFullStructureLocations",
        "GetDeviceProfiles",
        "GetFullStructureLocations",
    ]
    new_device = harness.cloud.account.device("NEW0000001")
    assert new_device is not None
    assert new_device.profile is None


@pytest.mark.asyncio
async def test_unknown_profile_reloads_catalog_only_once(harness: Harness) -> None:
    """A profile the reloaded catalog lacks does not reload it again."""
    await harness.cloud.async_start()
    harness.transport.answers["GetFullStructureLocations"] = [
        structure_response(location(SID, devices=(device("NEW0000001", UUID(int=77)),)))
    ]
    harness.transport.calls.clear()

    await harness.cloud.async_refresh()
    await harness.cloud.async_refresh()

    assert harness.transport.methods() == [
        "GetFullStructureLocations",
        "GetDeviceProfiles",
        "GetFullStructureLocations",
        "GetFullStructureLocations",
    ]


@pytest.mark.asyncio
async def test_reconnect_after_drop(harness: Harness) -> None:
    """A dropped channel reconnects with a fresh wstoken and polls again."""
    await harness.cloud.async_start()
    harness.transport.answers["GetFullStructureLocations"] = [
        cloud_structure(wstoken="ws-2")
    ]
    first = harness.broker.last
    disconnected: list[bool] = []
    harness.cloud.add_listener(lambda: disconnected.append(harness.cloud.connected))

    first.drop(TionConnectionError("gone"))
    await _eventually(lambda: len(harness.broker.connections) == 2)
    await _eventually(lambda: harness.cloud.connected)

    assert disconnected[0] is False
    assert harness.sleeps == [1.0]
    assert harness.broker.last.kwargs["auth_token"] == "access-1:ws-2"
    assert harness.broker.last.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        pytest.param(2, [1.0, 2.0, 4.0], id="doubling"),
        pytest.param(
            8, [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0], id="capped"
        ),
    ],
)
@pytest.mark.asyncio
async def test_reconnect_backs_off(
    harness: Harness, failures: int, expected: list[float]
) -> None:
    """Failed attempts wait 1, 2, 4 ... seconds, at most 60."""
    await harness.cloud.async_start()
    harness.broker.connect_errors = [TionConnectionError("down")] * failures

    harness.broker.last.drop(TionConnectionError("gone"))
    await _eventually(lambda: harness.cloud.connected)

    assert harness.sleeps == expected


@pytest.mark.asyncio
async def test_reconnect_backoff_resets_after_success(harness: Harness) -> None:
    """The next disconnect starts again at the shortest pause."""
    await harness.cloud.async_start()

    for reason in ("gone", "gone again"):
        harness.broker.connect_errors = [TionConnectionError("down")]
        harness.broker.last.drop(TionConnectionError(reason))
        await _eventually(lambda: harness.cloud.connected)

    assert harness.sleeps == [1.0, 2.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_reconnect_survives_unexpected_error(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """A bug in one attempt is logged and the next attempt still runs."""
    await harness.cloud.async_start()
    harness.broker.connect_errors = [RuntimeError("connector bug")]

    harness.broker.last.drop(TionConnectionError("gone"))
    await _eventually(lambda: harness.cloud.connected)

    assert harness.sleeps == [1.0, 2.0]
    assert "connector bug" in caplog.text


@pytest.mark.asyncio
async def test_reconnect_survives_unreachable_renewal(harness: Harness) -> None:
    """A token renewal that cannot reach the server is only a failed attempt."""
    await harness.cloud.async_start()
    harness.broker.connect_errors = [TionAuthError("Authorization Violation")]
    harness.auth.renew_error = TionConnectionError("offline")

    harness.broker.last.drop(TionConnectionError("gone"))
    await _eventually(lambda: harness.cloud.connected)

    assert harness.sleeps == [1.0, 2.0]
    assert harness.cloud.auth_error is None
    assert len(harness.broker.connections) == 2


@pytest.mark.asyncio
async def test_rpc_auth_error_renews_once(harness: Harness) -> None:
    """A rejected token is renewed once and the call retried."""
    harness.transport.answers["GetDeviceProfiles"] = [
        TionAuthError("401"),
        PROFILES,
    ]

    await harness.cloud.async_start()

    assert harness.transport.calls[:2] == [
        ("GetDeviceProfiles", "access-1"),
        ("GetDeviceProfiles", "access-2"),
    ]
    assert harness.auth.renewals == 1


@pytest.mark.asyncio
async def test_nats_auth_error_renews_and_rereadscloud_structure(
    harness: Harness,
) -> None:
    """A refused CONNECT renews the token and fetches a matching wstoken."""
    harness.broker.connect_errors = [TionAuthError("Authorization Violation")]
    harness.transport.answers["GetFullStructureLocations"] = [
        cloud_structure(wstoken="ws-1"),
        cloud_structure(wstoken="ws-2"),
    ]

    await harness.cloud.async_start()

    assert harness.broker.last.kwargs["auth_token"] == "access-2:ws-2"
    assert harness.transport.calls[-1] == ("GetFullStructureLocations", "access-2")


@pytest.mark.asyncio
async def test_start_auth_failure_raises_and_cleans_up(harness: Harness) -> None:
    """A login the server keeps refusing fails the start without leftovers."""
    harness.broker.connect_errors = [
        TionAuthError("Authorization Violation"),
        TionAuthError("Authorization Violation"),
    ]

    with pytest.raises(TionAuthError):
        await harness.cloud.async_start()

    assert harness.broker.connections == []
    assert not harness.cloud.connected


@pytest.mark.asyncio
async def test_start_failure_after_connect_stops_everything(
    harness: Harness,
) -> None:
    """If the channel drops during the first poll, nothing keeps running."""
    original = harness.broker.on_publish

    def drop_on_first_query(connection: FakeNats, subject: str, payload: bytes) -> None:
        connection.drop(TionConnectionError("gone"))

    harness.broker.on_publish = drop_on_first_query

    with pytest.raises(TionConnectionError):
        await harness.cloud.async_start()
    harness.broker.on_publish = original
    await asyncio.sleep(0)

    assert len(harness.broker.connections) == 1
    assert harness.sleeps == []


@pytest.mark.parametrize("break_poll", BROKEN_POLLS)
@pytest.mark.asyncio
async def test_start_fails_when_channel_breaks_during_poll(
    harness: Harness, break_poll: Callable[[Harness], None]
) -> None:
    """A channel that breaks while connecting fails the start and is closed."""
    break_poll(harness)

    with pytest.raises(TionConnectionError):
        await harness.cloud.async_start()
    await asyncio.sleep(0)

    connection = harness.broker.last
    assert connection.close_calls == 1
    assert not harness.cloud.connected
    assert len(harness.broker.connections) == 1
    assert harness.sleeps == []
    published = len(connection.published)
    harness.transport.calls.clear()
    await harness.cloud.async_refresh()
    assert harness.transport.methods() == ["GetFullStructureLocations"]
    assert len(connection.published) == published


@pytest.mark.parametrize("break_poll", BROKEN_POLLS)
@pytest.mark.asyncio
async def test_reconnect_retries_when_channel_breaks_during_poll(
    harness: Harness, break_poll: Callable[[Harness], None]
) -> None:
    """A channel that breaks while reconnecting counts as a failed attempt."""
    await harness.cloud.async_start()
    first = harness.broker.last
    break_poll(harness)

    first.drop(TionConnectionError("gone"))
    await _eventually(lambda: harness.cloud.connected)

    assert harness.sleeps == [1.0, 2.0]
    assert len(harness.broker.connections) == 3
    assert [connection.close_calls for connection in harness.broker.connections] == [
        0,
        1,
        0,
    ]
    assert harness.broker.last.subjects() == [f"app.location.{SID}.*", f"hw.tx.{SID}.>"]


@pytest.mark.asyncio
async def test_reconnect_retries_when_lost_during_login(harness: Harness) -> None:
    """A connection already lost when handed over is never reported connected."""
    harness.transport.answers["GetFullStructureLocations"] = [structure_response()]
    await harness.cloud.async_start()
    harness.broker.logins_lost = 1

    harness.broker.last.drop(TionConnectionError("gone"))
    await _eventually(lambda: harness.cloud.connected)

    assert harness.sleeps == [1.0, 2.0]
    assert len(harness.broker.connections) == 3
    assert not harness.broker.last.closed


@pytest_asyncio.fixture
async def hanging_up_nats_url() -> AsyncIterator[str]:
    """A NATS server that accepts the login, then reports an error and hangs up."""

    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=("maprot",))
        await ws.prepare(request)
        await ws.send_bytes(b"INFO {}\r\n")
        async for message in ws:
            if message.type is WSMsgType.BINARY and message.data.startswith(b"CONNECT"):
                await ws.send_bytes(b"PONG\r\n")
                await ws.send_bytes(b"-ERR 'Stale Connection'\r\n")
                await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield str(server.make_url("/")).replace("http://", "ws://")
    await server.close()


@pytest.mark.parametrize("create_task", ["eager"], indirect=True)
@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.asyncio
async def test_start_fails_when_real_channel_is_lost_during_login(
    create_task: TaskFactory | None, hanging_up_nats_url: str
) -> None:
    """An eager reader that sees the loss inside the connect fails the start."""
    transport = FakeTransport()
    transport.answers["GetFullStructureLocations"] = [structure_response()]
    transport.ssl_context = True

    async with ClientSession() as session:
        transport.session = session
        cloud = TionCloud(
            transport,
            FakeAuth(),
            create_task=create_task,
            connect=partial(NatsConnection.async_connect, url=hanging_up_nats_url),
        )
        with pytest.raises(TionConnectionError):
            await cloud.async_start()

    assert not cloud.connected


@pytest.mark.asyncio
async def test_connected_only_after_the_poll(harness: Harness) -> None:
    """The channel is reported connected once subscriptions and poll are done."""
    seen: list[bool] = []
    original = harness.broker.on_publish

    def record(connection: FakeNats, subject: str, payload: bytes) -> None:
        seen.append(harness.cloud.connected)
        original(connection, subject, payload)

    harness.broker.on_publish = record

    await harness.cloud.async_start()

    assert seen == [False, False]
    assert harness.cloud.connected


@pytest.mark.asyncio
async def test_stale_disconnect_is_ignored(harness: Harness) -> None:
    """A late drop report of a replaced connection leaves the new one alone."""
    await harness.cloud.async_start()
    first = harness.broker.last
    first.drop(TionConnectionError("gone"))
    await _eventually(lambda: harness.cloud.connected)
    second = harness.broker.last
    before = harness.notifications
    published = len(second.published)

    first.kwargs["on_disconnect"](TionConnectionError("stale"))
    await asyncio.sleep(0)

    assert harness.cloud.connected
    assert harness.notifications == before
    assert harness.sleeps == [1.0]
    assert len(harness.broker.connections) == 2
    await harness.cloud.async_refresh()
    assert len(second.published) > published
    assert second.close_calls == 0


@pytest.mark.asyncio
async def test_reconnect_auth_failure_sets_auth_error(harness: Harness) -> None:
    """A login refused in the background stops reconnecting and is reported."""
    await harness.cloud.async_start()
    harness.broker.connect_errors = [TionAuthError("Authorization Violation")]
    harness.auth.renew_error = TionAuthError("Renew session expired")
    notified: list[TionAuthError | None] = []
    harness.cloud.add_listener(lambda: notified.append(harness.cloud.auth_error))

    harness.broker.last.drop(TionAuthError("User Authentication Expired"))
    await _eventually(lambda: harness.cloud.auth_error is not None)

    assert str(harness.cloud.auth_error) == "Renew session expired"
    assert notified[-1] is harness.cloud.auth_error
    assert len(harness.broker.connections) == 1
    assert not harness.cloud.connected


@pytest.mark.asyncio
async def test_stop(harness: Harness) -> None:
    """Stop closes the channel; a late drop does not reconnect."""
    await harness.cloud.async_start()
    connection = harness.broker.last

    await harness.cloud.async_stop()
    connection.kwargs["on_disconnect"](TionConnectionError("late"))
    await asyncio.sleep(0)

    assert connection.closed
    assert not harness.cloud.connected
    assert len(harness.broker.connections) == 1
    assert harness.sleeps == []


@pytest.mark.asyncio
async def test_listeners(harness: Harness) -> None:
    """A failing listener does not stop others; unsubscribing works."""
    calls: list[str] = []

    def broken() -> None:
        raise RuntimeError("listener bug")

    harness.cloud.add_listener(broken)
    unsubscribe = harness.cloud.add_listener(lambda: calls.append("seen"))
    await harness.cloud.async_start()
    unsubscribe()
    count = len(calls)

    harness.broker.last.deliver(
        f"hw.tx.{SID}.dps.{BREEZER}",
        state_report(BREEZER, DPValue(140, DPKind.INT, 2)),
    )

    assert count > 0
    assert len(calls) == count


@pytest.mark.asyncio
async def test_command_success(harness: Harness) -> None:
    """A command is published and the answer's state lands in the snapshot."""
    await harness.cloud.async_start()
    connection = harness.broker.last
    connection.published.clear()
    before = harness.notifications

    await harness.cloud.async_command(harness.breezer().command(speed=3))

    ((subject, payload),) = connection.published
    assert subject == f"hw.rx.{SID}.dpu.{BREEZER}"
    request = ProtoMessage.parse(payload)
    assert payload == encode_update_request(
        request.get_int(2), int(SERVER_TIME), [DPValue(140, DPKind.INT, 3)]
    )
    assert harness.breezer().speed == 3
    assert harness.notifications > before


@pytest.mark.asyncio
async def test_command_refused(harness: Harness) -> None:
    """A device refusal raises TionCommandError with its code."""
    await harness.cloud.async_start()
    harness.broker.command_error = (5, "busy")

    with pytest.raises(TionCommandError) as caught:
        await harness.cloud.async_command(harness.breezer().command(speed=3))

    assert (caught.value.code, caught.value.message) == (5, "busy")
    assert harness.breezer().speed == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_replies")
async def test_command_unanswered(harness: Harness) -> None:
    """A device that does not answer in time is a connection error."""
    await harness.cloud.async_start()
    harness.broker.answer_commands = False

    with pytest.raises(TionConnectionError):
        await harness.cloud.async_command(harness.breezer().command(speed=3))


@pytest.mark.asyncio
async def test_command_when_disconnected(harness: Harness) -> None:
    """Without the live channel commands fail at once."""
    await harness.cloud.async_start()
    command = harness.breezer().command(speed=3)
    await harness.cloud.async_stop()

    with pytest.raises(TionConnectionError):
        await harness.cloud.async_command(command)


@pytest.mark.asyncio
async def test_command_interrupted_by_drop(harness: Harness) -> None:
    """A drop while waiting for the answer fails the command."""
    await harness.cloud.async_start()

    def drop_on_command(connection: FakeNats, subject: str, payload: bytes) -> None:
        connection.drop(TionConnectionError("gone"))

    harness.broker.on_publish = drop_on_command

    with pytest.raises(TionConnectionError):
        await harness.cloud.async_command(harness.breezer().command(speed=3))
    # The drop started reconnecting; stop it before the test ends.
    await harness.cloud.async_stop()


@pytest.mark.asyncio
async def test_command_for_unknown_device(harness: Harness) -> None:
    """Commands must target a device of the account."""
    await harness.cloud.async_start()

    with pytest.raises(ValueError):
        await harness.cloud.async_command(
            DeviceCommand("NOPE000001", (DPValue(70, DPKind.BOOL, True),))
        )


def _sent_auto_controls(harness: Harness) -> list[bytes]:
    return [
        payload
        for method, payload in harness.transport.payloads
        if method == "SetAutoControlParams"
    ]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param({"enabled": True}, AutoControl(True, 1, 5, 800, 1), id="enable"),
        pytest.param(
            {"speed_min": 2, "speed_max": 4},
            AutoControl(False, 2, 4, 800, 1),
            id="limits",
        ),
    ],
)
@pytest.mark.asyncio
async def test_set_auto_control_merges(
    harness: Harness, changes: dict[str, Any], expected: AutoControl
) -> None:
    """Partial changes are merged onto the room's current auto mode."""
    await harness.cloud.async_start()

    await harness.cloud.async_set_auto_control(ROOM_ID, **changes)

    assert _sent_auto_controls(harness) == [encode_set_auto_control(ROOM_ID, expected)]
    room_info = harness.cloud.account.room(ROOM_ID)
    assert room_info is not None
    assert room_info.auto == expected


@pytest.mark.asyncio
async def test_set_auto_control_serializes_per_room(harness: Harness) -> None:
    """Concurrent partial changes of one room do not lose a field."""
    await harness.cloud.async_start()

    await asyncio.gather(
        harness.cloud.async_set_auto_control(ROOM_ID, enabled=True),
        harness.cloud.async_set_auto_control(ROOM_ID, co2_target=900),
    )

    assert _sent_auto_controls(harness)[-1] == encode_set_auto_control(
        ROOM_ID, AutoControl(True, 1, 5, 900, 1)
    )


@pytest.mark.asyncio
async def test_set_auto_control_server_error(harness: Harness) -> None:
    """A refusal raises and leaves the room unchanged."""
    await harness.cloud.async_start()
    harness.transport.answers["SetAutoControlParams"] = [error_response(4, "busy")]

    with pytest.raises(TionApiError):
        await harness.cloud.async_set_auto_control(ROOM_ID, enabled=True)

    room_info = harness.cloud.account.room(ROOM_ID)
    assert room_info is not None
    assert room_info.auto == AutoControl(False, 1, 5, 800, 1)


@pytest.mark.parametrize(
    "auto",
    [
        pytest.param(None, id="no_auto"),
        pytest.param(b"", id="empty_auto"),
        pytest.param(
            auto_control(enabled=True, speed_min=0, speed_max=0, co2_target=0),
            id="zero_speed_max",
        ),
    ],
)
@pytest.mark.asyncio
async def test_set_auto_control_on_room_without_auto(
    harness: Harness, auto: bytes | None
) -> None:
    """A room without a usable auto mode needs every field; AVERAGE is assumed."""
    harness.transport.answers["GetFullStructureLocations"] = [
        structure_response(location(SID, rooms=(room(auto=auto),)))
    ]
    await harness.cloud.async_start()

    with pytest.raises(ValueError):
        await harness.cloud.async_set_auto_control(ROOM_ID, enabled=True)
    await harness.cloud.async_set_auto_control(
        ROOM_ID, enabled=True, speed_min=1, speed_max=3, co2_target=800
    )

    room_info = harness.cloud.account.room(ROOM_ID)
    assert room_info is not None
    assert room_info.auto == AutoControl(True, 1, 3, 800, 1)


@pytest.mark.parametrize(
    ("algorithm", "expected"),
    [
        pytest.param(0, 1, id="unset"),
        pytest.param(2, 2, id="maximum"),
        pytest.param(7, 1, id="unknown"),
    ],
)
@pytest.mark.asyncio
async def test_set_auto_control_algorithm(
    harness: Harness, algorithm: int, expected: int
) -> None:
    """A known algorithm is kept; anything else is written as AVERAGE."""
    harness.transport.answers["GetFullStructureLocations"] = [
        structure_response(
            location(
                SID,
                rooms=(
                    room(
                        auto=auto_control(
                            enabled=False,
                            speed_min=1,
                            speed_max=5,
                            co2_target=800,
                            algorithm=algorithm,
                        )
                    ),
                ),
            )
        )
    ]
    await harness.cloud.async_start()

    await harness.cloud.async_set_auto_control(ROOM_ID, enabled=True)

    assert _sent_auto_controls(harness) == [
        encode_set_auto_control(ROOM_ID, AutoControl(True, 1, 5, 800, expected))
    ]


@pytest.mark.parametrize(
    ("room_id", "changes"),
    [
        pytest.param(ROOM_ID, {"speed_min": 6}, id="min_above_max"),
        pytest.param(UUID(int=404), {"enabled": True}, id="unknown_room"),
    ],
)
@pytest.mark.asyncio
async def test_set_auto_control_rejects(
    harness: Harness, room_id: UUID, changes: dict[str, Any]
) -> None:
    """Invalid requests fail before reaching the server."""
    await harness.cloud.async_start()

    with pytest.raises(ValueError):
        await harness.cloud.async_set_auto_control(room_id, **changes)

    assert _sent_auto_controls(harness) == []


@pytest.mark.asyncio
async def test_command_uses_device_location(harness: Harness) -> None:
    """A device of a second location is addressed on that location's subject."""
    harness.transport.answers["GetFullStructureLocations"] = [
        cloud_structure(extra_location=True)
    ]
    await harness.cloud.async_start()
    connection = harness.broker.last
    connection.published.clear()
    second = harness.cloud.account.device("BRZ0000002")
    assert second is not None
    second_view = view(second)
    assert isinstance(second_view, Breezer)

    await harness.cloud.async_command(second_view.command(is_on=True))

    assert [subject for subject, _ in connection.published] == [
        "hw.rx.LOC0000002.dpu.BRZ0000002"
    ]


def _location_ids(harness: Harness, method: str) -> list[UUID | None]:
    return [
        location_id
        for called, location_id in harness.transport.location_ids
        if called == method
    ]


@pytest.mark.asyncio
async def test_set_auto_control_sends_the_rooms_location_id(
    harness: Harness,
) -> None:
    """The room RPC names the location that holds the room."""
    await harness.cloud.async_start()

    await harness.cloud.async_set_auto_control(ROOM_ID, enabled=True)

    assert _location_ids(harness, "SetAutoControlParams") == [LOCATION_ID]


@pytest.mark.asyncio
async def test_set_auto_control_uses_room_location(harness: Harness) -> None:
    """A room of a second location sends that location's id."""
    harness.transport.answers["GetFullStructureLocations"] = [
        cloud_structure(extra_location=True)
    ]
    await harness.cloud.async_start()

    await harness.cloud.async_set_auto_control(SECOND_ROOM_ID, enabled=True)

    assert _location_ids(harness, "SetAutoControlParams") == [SECOND_LOCATION_ID]


@pytest.mark.asyncio
async def test_set_auto_control_retry_keeps_location_id(harness: Harness) -> None:
    """The retry after a token renewal still carries the location id."""
    await harness.cloud.async_start()
    harness.transport.answers["SetAutoControlParams"] = [
        TionAuthError("401"),
        encode_varint(1, 1),
    ]

    await harness.cloud.async_set_auto_control(ROOM_ID, enabled=True)

    assert _location_ids(harness, "SetAutoControlParams") == [LOCATION_ID] * 2


@pytest.mark.asyncio
async def test_structure_and_profile_calls_have_no_location_id(
    harness: Harness,
) -> None:
    """Only room RPCs are location-scoped."""
    await harness.cloud.async_start()

    assert _location_ids(harness, "GetFullStructureLocations") == [None]
    assert _location_ids(harness, "GetDeviceProfiles") == [None]
