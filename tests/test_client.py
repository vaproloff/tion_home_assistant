"""Tests for the Tion API client."""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Self

from aiohttp import ClientError
import pytest

from custom_components.tion.client import (
    API2_PROFILE,
    API_PROFILE,
    DEFAULT_PROFILE,
    PROFILES,
    PROFILES_BY_NAME,
    TionApiError,
    TionApiProfile,
    TionClient,
    TionConnectionError,
    TionLocation,
)


class FakeResponse:
    """Async-context-manager stand-in for an aiohttp response."""

    def __init__(self, status: int, payload: Any, text_body: str = "") -> None:
        """Store the canned status and JSON payload (or exception)."""
        self.status = status
        self._payload = payload
        self._text_body = text_body

    async def json(self, content_type: str | None = None) -> Any:
        """Return the canned payload, raising it if it is an exception."""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def text(self) -> str:
        """Return the canned raw response body."""
        return self._text_body

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(self, *exc: object) -> bool:
        """Exit the async context."""
        return False


class FakeSession:
    """Routes requests by endpoint host to canned responses or exceptions.

    `routes` maps a host substring ("api." / "api2.") to a callable
    `(method, url, kwargs) -> FakeResponse` which may raise.
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        """Store the host->handler routes and start an empty call log."""
        self.routes = routes
        self.calls: list[SimpleNamespace] = []

    def request(
        self,
        method: str,
        *,
        url: str,
        headers: dict[str, str],
        timeout: int,
        **kwargs: Any,
    ) -> FakeResponse:
        """Route a request to its canned response by endpoint host."""
        self.calls.append(
            SimpleNamespace(method=method, url=url, headers=headers, kwargs=kwargs)
        )
        host = "api2." if "//api2." in url else "api."
        result = self.routes[host](method, url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result


def _token_response() -> FakeResponse:
    return FakeResponse(200, {"token_type": "Bearer", "access_token": "tok"})


def _make_client(
    routes: dict[str, Any], **kwargs: Any
) -> tuple[TionClient, FakeSession]:
    session = FakeSession(routes)
    client = TionClient(session, "user", "pass", **kwargs)
    return client, session


@pytest.mark.asyncio
async def test_auth_is_stored_per_profile_and_listener_gets_name_and_token() -> None:
    """Authenticating a profile stores its token under the profile name."""
    routes = {
        "api.": lambda *a: _token_response(),
        "api2.": lambda *a: _token_response(),
    }
    client, _ = _make_client(routes)
    seen: list[tuple[str, str]] = []

    async def _listener(name: str, token: str) -> None:
        seen.append((name, token))

    client.add_update_listener(_listener)

    token = await client.async_get_authorization()

    assert token == "Bearer tok"
    assert client.authorization == {"api": "Bearer tok", "api2": None}
    assert seen == [("api", "Bearer tok")]


@pytest.mark.asyncio
async def test_legacy_string_auth_is_migrated_to_api_profile() -> None:
    """A legacy string token is treated as the api profile's token."""
    routes = {
        "api.": lambda *a: _token_response(),
        "api2.": lambda *a: _token_response(),
    }
    client, _ = _make_client(routes, auth="Bearer legacy")

    assert client.authorization == {"api": "Bearer legacy", "api2": None}
    assert client.active_profile == "api"


@pytest.mark.asyncio
async def test_active_profile_can_be_restored_from_persisted_name() -> None:
    """The client starts on the persisted active profile."""
    routes = {
        "api.": lambda *a: _token_response(),
        "api2.": lambda *a: _token_response(),
    }
    client, _ = _make_client(routes, active_profile="api2")

    assert client.active_profile == "api2"


@pytest.mark.asyncio
async def test_request_uses_active_profile_endpoint_and_headers() -> None:
    """A request is sent to the active profile's endpoint with its headers."""
    routes = {
        "api.": lambda *a: FakeResponse(200, [{"guid": "loc"}]),
        "api2.": lambda *a: FakeResponse(200, [{"guid": "loc"}]),
    }
    client, session = _make_client(routes, auth={"api": "Bearer t", "api2": None})

    await client.get_locations()

    location_call = session.calls[-1]
    assert location_call.url == "https://api.magicair.tion.ru/Location"
    assert location_call.headers["Authorization"] == "Bearer t"
    assert "Content-Type" not in location_call.headers


def test_profiles_are_two_equivalent_endpoints() -> None:
    """Both profiles exist, are distinct, and api is the default."""
    assert PROFILES == [API_PROFILE, API2_PROFILE]
    assert PROFILES_BY_NAME == {"api": API_PROFILE, "api2": API2_PROFILE}
    assert DEFAULT_PROFILE is API_PROFILE
    assert API_PROFILE.endpoint == "https://api.magicair.tion.ru/"
    assert API2_PROFILE.endpoint == "https://api2.magicair.tion.ru/"
    assert API_PROFILE.grant_type == "extended"
    assert API2_PROFILE.grant_type == "password"
    assert API_PROFILE.scope is not None
    assert API2_PROFILE.scope is None


def test_base_headers_never_set_content_type() -> None:
    """Content-Type must be left to aiohttp so form auth is not broken."""
    for profile in PROFILES:
        assert isinstance(profile, TionApiProfile)
        assert "Content-Type" not in profile.base_headers
        assert profile.base_headers["Host"] == profile.host


def _fail_conn(*_a: Any) -> Exception:
    return ClientError("boom")


@pytest.mark.asyncio
async def test_request_fails_over_to_second_profile_and_sticks() -> None:
    """A connection error on the active profile switches to the other one."""
    routes = {
        "api.": _fail_conn,
        "api2.": lambda *a: FakeResponse(200, [{"guid": "loc"}]),
    }
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    await client.get_locations()

    assert client.active_profile == "api2"

    # Subsequent request stays on api2 without touching api again.
    await client.get_locations()
    assert client.active_profile == "api2"


@pytest.mark.asyncio
async def test_both_profiles_down_raises_and_tries_each_once() -> None:
    """When both profiles fail, the error propagates without looping."""
    calls: list[str] = []

    def record(host: str) -> Callable[..., Exception]:
        def handler(*_a: Any) -> Exception:
            calls.append(host)
            return ClientError("down")

        return handler

    routes = {"api.": record("api"), "api2.": record("api2")}
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    with pytest.raises(TionConnectionError):
        await client.get_locations()

    assert sorted(calls) == ["api", "api2"]


@pytest.mark.asyncio
async def test_read_failover_uses_correct_path_per_profile() -> None:
    """Read failover must use each profile's own location path (case differs)."""

    def api2_handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/location"):
            return FakeResponse(200, [{"guid": "loc"}])
        return FakeResponse(404, {})

    routes = {"api.": _fail_conn, "api2.": api2_handler}
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    locations = await client.get_locations()

    assert client.active_profile == "api2"
    assert len(locations) == 1
    assert locations[0].guid == "loc"


@pytest.mark.asyncio
async def test_concurrent_failover_does_not_skip_healthy_profile() -> None:
    """Two concurrent failovers must each reach the healthy profile."""
    gate = asyncio.Event()
    arrivals = {"count": 0}

    class GatedFailResponse:
        async def __aenter__(self) -> Self:
            arrivals["count"] += 1
            await gate.wait()
            raise ClientError("boom")

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def api_handler(method: str, url: str, kwargs: dict[str, Any]) -> GatedFailResponse:
        return GatedFailResponse()

    routes = {
        "api.": api_handler,
        "api2.": lambda *a: FakeResponse(200, [{"guid": "loc"}]),
    }
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    task1 = asyncio.create_task(client.get_locations())
    task2 = asyncio.create_task(client.get_locations())

    while arrivals["count"] < 2:
        await asyncio.sleep(0.01)

    gate.set()
    results = await asyncio.gather(task1, task2)

    assert client.active_profile == "api2"
    assert all(len(r) == 1 for r in results)
    assert all(r[0].guid == "loc" for r in results)


@pytest.mark.asyncio
async def test_no_failover_on_api_error() -> None:
    """A non-connection error (4xx) does not trigger failover."""
    routes = {
        "api.": lambda *a: FakeResponse(404, {}),
        "api2.": lambda *a: FakeResponse(200, [{"guid": "loc"}]),
    }
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    with pytest.raises(TionApiError):
        await client.get_locations()

    assert client.active_profile == "api"


def _queued_then_completed() -> Callable[[str, str, dict[str, Any]], FakeResponse]:
    """Handler: POST returns queued; GET task returns completed."""

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if method == "post":
            return FakeResponse(200, {"status": "queued", "task_id": "T1"})
        return FakeResponse(200, {"status": "completed"})

    return handler


@pytest.mark.asyncio
async def test_send_pins_task_poll_to_post_profile() -> None:
    """When api is down, the POST and its task poll both go to api2."""
    routes = {"api.": _fail_conn, "api2.": _queued_then_completed()}
    client, session = _make_client(routes, auth={"api": "t", "api2": "t2"})

    ok = await client.send_settings("guid-1", {"backlight": True})

    assert ok is True
    assert client.active_profile == "api2"
    task_calls = [c for c in session.calls if c.method == "get" and "/task/" in c.url]
    assert task_calls
    assert all("//api2." in c.url for c in task_calls)


@pytest.mark.asyncio
async def test_send_pins_task_poll_to_post_profile_when_active_changes() -> None:
    """Task polling stays on the POST profile even if another request switches back."""
    polling_api2 = asyncio.Event()
    release_poll = asyncio.Event()

    class GatedTaskResponse:
        """Task-poll response that pauses until released, then completes."""

        status = 200

        async def __aenter__(self) -> Self:
            polling_api2.set()
            await release_poll.wait()
            return self

        async def json(self, content_type: str | None = None) -> Any:
            return {"status": "completed"}

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def api_handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if method == "post":
            raise ClientError("api command down")
        if url.endswith("/Location"):
            return FakeResponse(200, [{"guid": "loc"}])
        return FakeResponse(404, {})

    def api2_handler(
        method: str, url: str, kwargs: dict[str, Any]
    ) -> FakeResponse | GatedTaskResponse:
        if method == "post":
            return FakeResponse(200, {"status": "queued", "task_id": "T1"})
        if url.endswith("/location"):
            raise ClientError("api2 read down")
        if "/task/T1" in url:
            return GatedTaskResponse()
        return FakeResponse(404, {})

    routes = {"api.": api_handler, "api2.": api2_handler}
    client, session = _make_client(routes, auth={"api": "t", "api2": "t2"})

    send_task = asyncio.create_task(client.send_settings("guid-1", {"backlight": True}))
    # The POST failed over to api2 (now active) and the task poll is in flight.
    await asyncio.wait_for(polling_api2.wait(), timeout=1)

    # A concurrent read fails over api2->api, flipping the active profile back.
    await client.get_locations()
    assert client.active_profile == "api"

    release_poll.set()
    assert await send_task is True

    task_calls = [call for call in session.calls if "/task/T1" in call.url]
    assert task_calls
    assert all("//api2." in call.url for call in task_calls)


@pytest.mark.asyncio
async def test_poll_connection_error_does_not_switch_profiles() -> None:
    """A connection error during task polling propagates without failover."""

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if method == "post":
            return FakeResponse(200, {"status": "queued", "task_id": "T1"})
        raise ClientError("poll dropped")

    routes = {"api.": handler, "api2.": lambda *a: FakeResponse(200, {})}
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    with pytest.raises(TionConnectionError):
        await client.send_settings("guid-1", {"backlight": True})

    assert client.active_profile == "api"


@pytest.mark.asyncio
async def test_validate_auth_returns_dict_for_config_entry() -> None:
    """async_validate_auth returns the per-profile dict the entry will store."""
    routes = {
        "api.": lambda method, url, kwargs: (
            _token_response()
            if "connect/token" in url
            else FakeResponse(200, [{"guid": "loc"}])
        ),
        "api2.": lambda *a: _token_response(),
    }
    client, _ = _make_client(routes)

    auth = await client.async_validate_auth()

    assert isinstance(auth, dict)
    assert auth["api"] == "Bearer tok"


@pytest.mark.asyncio
async def test_validate_auth_fails_over_to_api2_when_api_auth_is_down() -> None:
    """async_validate_auth uses failover when the default auth endpoint is down."""

    def api2_handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if "oauth2/token" in url:
            return FakeResponse(
                200, {"token_type": "Bearer", "access_token": "api2tok"}
            )
        if url.endswith("/location"):
            return FakeResponse(200, [{"guid": "loc"}])
        return FakeResponse(404, {})

    routes = {
        "api.": _fail_conn,
        "api2.": api2_handler,
    }
    client, _ = _make_client(routes)

    auth = await client.async_validate_auth()

    assert auth == {"api": None, "api2": "Bearer api2tok"}
    assert client.active_profile == "api2"


def _sample_location() -> TionLocation:
    """Return a location with one zone holding a breezer and a MagicAir."""
    return TionLocation(
        {
            "guid": "loc-guid",
            "name": "Home",
            "zones": [
                {
                    "guid": "zone-guid",
                    "name": "Bedroom",
                    "mode": {"current": "auto", "auto_set": {"co2": 800}},
                    "devices": [
                        {
                            "guid": "breezer-guid",
                            "name": "Breezer 4S",
                            "type": "breezer4s",
                            "mac": "AA:BB:CC:DD",
                            "is_online": True,
                            "data": {"data_valid": True, "speed": 2, "t_set": 20},
                        },
                        {
                            "guid": "station-guid",
                            "name": "MagicAir",
                            "type": "co2mb",
                            "mac": "EE:FF:00:11",
                            "is_online": False,
                            "data": {"data_valid": True, "co2": 950},
                        },
                    ],
                }
            ],
        }
    )


def test_location_log_summary_renders_zones_and_devices_by_name() -> None:
    """The summary names the location, zones and devices and their raw state."""
    summary = _sample_location().log_summary()

    assert "Home" in summary
    assert "Bedroom" in summary
    assert "mode=auto" in summary
    assert "target_co2=800" in summary
    assert "Breezer 4S" in summary
    assert "speed=2" in summary
    assert "MagicAir" in summary
    assert "co2=950" in summary
    # The offline gateway's raw state is visible, so reachability is readable.
    assert "online=False" in summary


def test_location_log_summary_omits_identifiers() -> None:
    """The summary must not leak guids or MAC addresses."""
    summary = _sample_location().log_summary()

    assert "loc-guid" not in summary
    assert "breezer-guid" not in summary
    assert "station-guid" not in summary
    assert "AA:BB:CC:DD" not in summary
    assert "EE:FF:00:11" not in summary


@pytest.mark.asyncio
async def test_api_error_includes_response_body() -> None:
    """A 4xx error surfaces the cloud's JSON error body for diagnosis."""
    routes = {
        "api.": lambda *a: FakeResponse(400, {"error": "bad co2 format"}),
        "api2.": lambda *a: FakeResponse(200, [{"guid": "loc"}]),
    }
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    with pytest.raises(TionApiError, match="bad co2 format"):
        await client.get_locations()


@pytest.mark.asyncio
async def test_server_error_includes_non_json_body() -> None:
    """A 5xx error with a non-JSON body still surfaces the raw text."""

    def server_error(*_a: Any) -> FakeResponse:
        return FakeResponse(
            500, ValueError("not json"), text_body="Internal Server Error"
        )

    routes = {"api.": server_error, "api2.": server_error}
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    with pytest.raises(TionConnectionError, match="Internal Server Error"):
        await client.get_locations()


@pytest.mark.asyncio
async def test_connection_error_names_underlying_failure() -> None:
    """A transport failure names the underlying error instead of hiding it."""
    routes = {"api.": _fail_conn, "api2.": _fail_conn}
    client, _ = _make_client(routes, auth={"api": "t", "api2": "t2"})

    with pytest.raises(TionConnectionError, match="ClientError"):
        await client.get_locations()
