"""Tests for the Tion API client."""

from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import ClientError

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
)


class FakeResponse:
    """Async-context-manager stand-in for an aiohttp response."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: str | None = None) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Routes requests by endpoint host to canned responses or exceptions.

    `routes` maps a host substring ("api." / "api2.") to a callable
    `(method, url, kwargs) -> FakeResponse` which may raise.
    """

    def __init__(
        self, routes: dict[str, Any]
    ) -> None:
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


def _make_client(routes: dict[str, Any], **kwargs: Any) -> tuple[TionClient, FakeSession]:
    session = FakeSession(routes)
    client = TionClient(session, "user", "pass", **kwargs)
    return client, session


@pytest.mark.asyncio
async def test_auth_is_stored_per_profile_and_listener_gets_name_and_token() -> None:
    """Authenticating a profile stores its token under the profile name."""
    routes = {"api.": lambda *a: _token_response(), "api2.": lambda *a: _token_response()}
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
    routes = {"api.": lambda *a: _token_response(), "api2.": lambda *a: _token_response()}
    client, _ = _make_client(routes, auth="Bearer legacy")

    assert client.authorization == {"api": "Bearer legacy", "api2": None}
    assert client.active_profile == "api"


@pytest.mark.asyncio
async def test_active_profile_can_be_restored_from_persisted_name() -> None:
    """The client starts on the persisted active profile."""
    routes = {"api.": lambda *a: _token_response(), "api2.": lambda *a: _token_response()}
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
    switches: list[str] = []

    async def _on_switch(name: str) -> None:
        switches.append(name)

    client.add_active_profile_listener(_on_switch)

    await client.get_locations()

    assert client.active_profile == "api2"
    assert switches == ["api2"]

    # Subsequent request stays on api2 without touching api again.
    await client.get_locations()
    assert client.active_profile == "api2"
    assert switches == ["api2"]


@pytest.mark.asyncio
async def test_both_profiles_down_raises_and_tries_each_once() -> None:
    """When both profiles fail, the error propagates without looping."""
    calls: list[str] = []

    def record(host: str):
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


def _queued_then_completed():
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
