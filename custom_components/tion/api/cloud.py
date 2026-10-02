"""TionCloud: the account's live state over gRPC and NATS, and its commands."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from functools import partial
import logging
from typing import Any
from uuid import UUID

from .auth import TionAuth
from .datapoints import (
    SERVICE_STATE,
    SERVICE_UPDATE,
    DPUpdateResponse,
    DPValue,
    decode_state_report,
    decode_update_response,
    device_reports_subject,
    device_subject,
    encode_state_query,
    encode_update_request,
    location_events_subject,
    new_command_id,
    parse_device_subject,
    parse_location_event,
)
from .exceptions import (
    TionApiError,
    TionAuthError,
    TionCommandError,
    TionConnectionError,
    TionError,
)
from .model import AutoControl, AutoControlAlgorithm, Device, Location, TionAccount
from .nats import NatsConnection, TaskFactory
from .profiles import METHOD_GET_PROFILES, SVC_PROFILES, DeviceProfile, decode_profiles
from .structure import (
    EVENT_AUTO_CONTROL_CHANGED,
    METHOD_GET_STRUCTURE,
    METHOD_SET_AUTO_CONTROL,
    SVC_LOCATION_READER,
    SVC_ROOM_WRITER,
    Structure,
    check_set_auto_control,
    decode_auto_control_changed,
    decode_structure,
    encode_set_auto_control,
)
from .transport import TionTransport
from .views import DeviceCommand, view

_LOGGER = logging.getLogger(__name__)

REPLY_TIMEOUT = 10.0
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
STRUCTURE_REFRESH_DELAY = 5.0
NATS_USER_PREFIX = "mapp"

type Connector = Callable[..., Awaitable[NatsConnection]]


def _default_task_factory(
    coro: Coroutine[Any, Any, None], name: str
) -> asyncio.Task[None]:
    return asyncio.create_task(coro, name=name)


def _discard(future: asyncio.Future[Any]) -> None:
    """Cancel an unanswered reply, or mark a failed one as retrieved."""
    if not future.done():
        future.cancel()
    elif not future.cancelled():
        future.exception()


class TionCloud:
    """Live view of a Tion account and the commands that change it."""

    def __init__(
        self,
        transport: TionTransport,
        auth: TionAuth,
        *,
        create_task: TaskFactory | None = None,
        connect: Connector = NatsConnection.async_connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Bind the cloud to a transport and a logged-in session."""
        self._transport = transport
        self._auth = auth
        self._create_task = create_task or _default_task_factory
        self._connect_nats = connect
        self._sleep = sleep
        self._profiles: dict[UUID, DeviceProfile] = {}
        self._locations: tuple[Location, ...] = ()
        self._wstoken = ""
        self._dps: dict[str, dict[int, DPValue]] = {}
        self._account = TionAccount()
        self._connection: NatsConnection | None = None
        self._attempt: object | None = None
        self._subscriptions: dict[str, list[str]] = {}
        self._pending_queries: dict[int, asyncio.Future[None]] = {}
        self._pending_commands: dict[int, asyncio.Future[DPUpdateResponse]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._room_locks: dict[UUID, asyncio.Lock] = {}
        # Serializes structure reloads with the subscription sync that follows.
        self._structure_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._structure_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopped = False
        self.auth_error: TionAuthError | None = None

    @property
    def account(self) -> TionAccount:
        """Return the latest snapshot of the account."""
        return self._account

    @property
    def connected(self) -> bool:
        """Return True while the live channel is up."""
        return self._account.connected

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener after every change of the snapshot; returns unsubscribe."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_start(self) -> None:
        """Load profiles and structure, open the live channel, poll devices."""
        self._stopped = False
        self.auth_error = None
        try:
            await self._load_profiles()
            await self._connect()
        except BaseException:
            await self.async_stop()
            raise
        self._started = True

    async def async_refresh(self) -> None:
        """Re-read the structure and poll every device again."""
        async with self._structure_lock:
            token = await self._auth.async_ensure_valid()
            try:
                await self._load_structure(token)
            except TionAuthError:
                await self._load_structure(
                    (await self._auth.async_renew_access()).access_token
                )
            if (connection := self._connection) is not None:
                await self._sync_subscriptions(connection)
        if self._connection is not None:
            await self._query_all()
        self._rebuild()

    async def async_stop(self) -> None:
        """Close the live channel and cancel background work."""
        self._started = False
        self._stopped = True
        for task in (self._reconnect_task, self._structure_task):
            if task is not None:
                task.cancel()
        self._fail_pending(TionConnectionError("Tion cloud stopped"))
        self._attempt = None
        if (connection := self._connection) is not None:
            self._connection = None
            await connection.async_close()
        self._set_connected(False)

    async def async_command(self, command: DeviceCommand) -> None:
        """Send datapoint values to a device and wait for its answer."""
        device = self._account.device(command.device_id)
        location = self._account.location_of(device) if device is not None else None
        if location is None:
            raise ValueError(f"Unknown device {command.device_id}")
        if (connection := self._connection) is None:
            raise TionConnectionError("Tion live channel is not connected")
        command_id = new_command_id()
        future: asyncio.Future[DPUpdateResponse] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_commands[command_id] = future
        try:
            await connection.async_publish(
                device_subject(location.sid, SERVICE_UPDATE, command.device_id),
                encode_update_request(
                    command_id, int(self._transport.server_time()), command.values
                ),
            )
            async with asyncio.timeout(REPLY_TIMEOUT):
                response = await future
        except TimeoutError as err:
            raise TionConnectionError(
                f"Device {command.device_id} did not answer the command"
            ) from err
        finally:
            self._pending_commands.pop(command_id, None)
            _discard(future)
        if not response.success:
            raise TionCommandError(response.error_code, response.error_message)

    async def async_set_auto_control(
        self,
        room_id: UUID,
        *,
        enabled: bool | None = None,
        speed_min: int | None = None,
        speed_max: int | None = None,
        co2_target: int | None = None,
    ) -> None:
        """Change a room's auto mode; unset fields keep their current values."""
        changes = {
            name: value
            for name, value in (
                ("enabled", enabled),
                ("speed_min", speed_min),
                ("speed_max", speed_max),
                ("co2_target", co2_target),
            )
            if value is not None
        }
        async with self._room_locks.setdefault(room_id, asyncio.Lock()):
            if (room := self._account.room(room_id)) is None:
                raise ValueError(f"Unknown room {room_id}")
            # speed_max 0 is an unset auto mode (e.g. an empty message), not settings.
            if (current := room.auto) is not None and current.speed_max > 0:
                if current.algorithm not in AutoControlAlgorithm:
                    current = replace(current, algorithm=AutoControlAlgorithm.AVERAGE)
                auto = replace(current, **changes)
            elif len(changes) == 4:
                auto = AutoControl(**changes)
            else:
                raise ValueError(f"Room {room_id} has no auto mode; pass every field")
            if auto.speed_min > auto.speed_max:
                raise ValueError("speed_min must not exceed speed_max")
            check_set_auto_control(
                await self._call(
                    SVC_ROOM_WRITER,
                    METHOD_SET_AUTO_CONTROL,
                    encode_set_auto_control(room_id, auto),
                )
            )
            self._update_room(room_id, lambda _: auto)

    async def _call(self, service: str, method: str, payload: bytes = b"") -> bytes:
        """Call an RPC, renewing the access token once if the server rejects it."""
        token = await self._auth.async_ensure_valid()
        try:
            return await self._transport.async_call(service, method, payload, token)
        except TionAuthError:
            tokens = await self._auth.async_renew_access()
            return await self._transport.async_call(
                service, method, payload, tokens.access_token
            )

    async def _load_profiles(self) -> None:
        self._profiles = decode_profiles(
            await self._call(SVC_PROFILES, METHOD_GET_PROFILES)
        )

    async def _load_structure(self, token: str) -> None:
        """Read the structure with this token; the wstoken is bound to it."""
        structure = await self._read_structure(token)
        if structure.profile_ids() - self._profiles.keys():
            await self._load_profiles()
            structure = await self._read_structure(token)
        self._locations = structure.locations
        self._wstoken = structure.wstoken

    async def _read_structure(self, token: str) -> Structure:
        payload = await self._transport.async_call(
            SVC_LOCATION_READER, METHOD_GET_STRUCTURE, b"", token
        )
        return decode_structure(payload, self._profiles)

    async def _connect(self) -> None:
        """Open the live channel, renewing the access token once if refused."""
        attempt = object()
        async with self._structure_lock:
            try:
                connection = await self._open_channel(attempt, renew=False)
            except TionAuthError:
                connection = await self._open_channel(attempt, renew=True)
            self._connection = connection
            self._attempt = attempt
            self._subscriptions = {}
            try:
                await self._sync_subscriptions(connection)
            except BaseException:
                await self._abandon(connection)
                raise
        try:
            await self._poll_connecting(connection)
        except BaseException:
            await self._abandon(connection)
            raise
        self._set_connected(True)

    async def _poll_connecting(self, connection: NatsConnection) -> None:
        await self._query_all()
        # An eager reader can lose the connection before async_connect returns,
        # when its disconnect report still looks stale.
        if self._connection is not connection or connection.closed:
            raise TionConnectionError("Live channel lost while connecting")

    async def _abandon(self, connection: NatsConnection) -> None:
        """Forget and close a connection whose setup failed."""
        if self._connection is connection:
            self._connection = None
            self._attempt = None
            self._subscriptions = {}
            self._fail_pending(TionConnectionError("Live channel closed"))
        await connection.async_close()

    async def _open_channel(self, attempt: object, *, renew: bool) -> NatsConnection:
        if renew:
            token = (await self._auth.async_renew_access()).access_token
        else:
            token = await self._auth.async_ensure_valid()
        await self._load_structure(token)
        return await self._connect_nats(
            self._transport.session,
            user=NATS_USER_PREFIX + self._auth.device_key.key_id(),
            auth_token=f"{token}:{self._wstoken}",
            on_disconnect=partial(self._on_disconnect, attempt),
            ssl_context=self._transport.ssl_context,
            create_task=self._create_task,
        )

    async def _sync_subscriptions(self, connection: NatsConnection) -> None:
        """Subscribe to new locations and drop vanished ones."""
        # A disconnect replaces the dict; keep working on this connection's one.
        subscriptions = self._subscriptions
        wanted = {location.sid for location in self._locations}
        for sid in set(subscriptions) - wanted:
            for subscription in subscriptions.pop(sid):
                await connection.async_unsubscribe(subscription)
        for sid in wanted - set(subscriptions):
            subscriptions[sid] = [
                await connection.async_subscribe(
                    device_reports_subject(sid), self._on_device_message
                ),
                await connection.async_subscribe(
                    location_events_subject(sid), self._on_location_event
                ),
            ]

    async def _query_all(self) -> None:
        await asyncio.gather(
            *(
                self._query(location, device)
                for location in self._locations
                for device in location.devices
                if view(device) is not None
            )
        )

    async def _query(self, location: Location, device: Device) -> None:
        """Ask a device for its datapoints; silence is not an error."""
        device_view = view(device)
        if device_view is None or (connection := self._connection) is None:
            return
        command_id = new_command_id()
        future = asyncio.get_running_loop().create_future()
        self._pending_queries[command_id] = future
        try:
            await connection.async_publish(
                device_subject(location.sid, SERVICE_STATE, device.id),
                encode_state_query(device_view.query_dp_ids(), command_id),
            )
            async with asyncio.timeout(REPLY_TIMEOUT):
                await future
        except TimeoutError:
            _LOGGER.debug("Device %s did not answer the state query", device.id)
        finally:
            self._pending_queries.pop(command_id, None)
            _discard(future)

    def _on_device_message(self, subject: str, payload: bytes) -> None:
        if (parsed := parse_device_subject(subject)) is None:
            return
        _, service, device_id = parsed
        try:
            if service == SERVICE_STATE:
                report = decode_state_report(payload)
                self._merge(device_id, report.dps)
                self._resolve(self._pending_queries, report.original_command_id, None)
            elif service == SERVICE_UPDATE:
                response = decode_update_response(payload)
                if response.success:
                    self._merge(device_id, response.dps)
                self._resolve(
                    self._pending_commands, response.original_command_id, response
                )
            else:
                _LOGGER.debug("Ignoring %s message from %s", service, device_id)
        except TionApiError as err:
            _LOGGER.warning("Skipping malformed message on %s: %s", subject, err)

    def _on_location_event(self, subject: str, payload: bytes) -> None:
        if (parsed := parse_location_event(subject)) is None:
            return
        _, event = parsed
        if event != EVENT_AUTO_CONTROL_CHANGED:
            self._schedule_structure_refresh()
            return
        try:
            change = decode_auto_control_changed(payload)
        except TionApiError as err:
            _LOGGER.warning("Skipping malformed %s: %s", event, err)
            return
        self._update_room(change.room_id, change.apply)

    @staticmethod
    def _resolve(pending: dict[int, asyncio.Future[Any]], key: int, value: Any) -> None:
        if (future := pending.get(key)) is not None and not future.done():
            future.set_result(value)

    def _merge(self, device_id: str, values: tuple[DPValue, ...]) -> None:
        """Apply reported values in arrival order (NATS keeps it per publisher)."""
        if not values:
            return
        current = self._dps.setdefault(device_id, {})
        for value in values:
            current[value.dp_id] = value
        self._rebuild()

    def _update_room(
        self,
        room_id: UUID,
        update: Callable[[AutoControl | None], AutoControl | None],
    ) -> None:
        """Replace the room's auto mode with update(current auto mode)."""
        self._locations = tuple(
            replace(
                location,
                rooms=tuple(
                    replace(room, auto=update(room.auto))
                    if room.id == room_id
                    else room
                    for room in location.rooms
                ),
            )
            for location in self._locations
        )
        self._rebuild()

    def _schedule_structure_refresh(self) -> None:
        if self._structure_task is not None and not self._structure_task.done():
            return
        self._structure_task = self._create_task(
            self._refresh_structure_later(), "tion_cloud_structure"
        )

    async def _refresh_structure_later(self) -> None:
        await self._sleep(STRUCTURE_REFRESH_DELAY)
        try:
            await self.async_refresh()
        except TionError as err:
            _LOGGER.debug("Structure refresh after a location event failed: %s", err)

    def _on_disconnect(self, attempt: object, error: TionError) -> None:
        if attempt is not self._attempt:
            _LOGGER.debug("Ignoring a stale live channel disconnect: %s", error)
            return
        _LOGGER.debug("Tion live channel lost: %s", error)
        self._connection = None
        self._attempt = None
        self._subscriptions = {}
        self._fail_pending(TionConnectionError(f"Live channel lost: {error}"))
        self._set_connected(False)
        # Before a successful start the caller sees the error instead.
        if not self._started or (
            self._reconnect_task is not None and not self._reconnect_task.done()
        ):
            return
        self._reconnect_task = self._create_task(
            self._reconnect(), "tion_cloud_reconnect"
        )

    async def _reconnect(self) -> None:
        attempt = 0
        while not self._stopped:
            await self._sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            try:
                await self._connect()
            except TionAuthError as err:
                self.auth_error = err
                self._notify()
                return
            except TionError as err:
                _LOGGER.debug("Reconnect attempt %s failed: %s", attempt + 1, err)
                attempt += 1
            else:
                return

    def _fail_pending(self, error: TionError) -> None:
        for pending in (self._pending_queries, self._pending_commands):
            for future in pending.values():
                if not future.done():
                    future.set_exception(error)
            pending.clear()

    def _set_connected(self, connected: bool) -> None:
        if self._account.connected != connected:
            self._rebuild(connected=connected)

    def _rebuild(self, *, connected: bool | None = None) -> None:
        locations = tuple(
            replace(
                location,
                devices=tuple(
                    replace(device, dps=dict(self._dps.get(device.id, {})))
                    for device in location.devices
                ),
            )
            for location in self._locations
        )
        self._account = TionAccount(
            locations,
            self._account.connected if connected is None else connected,
        )
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error in a Tion cloud listener")
