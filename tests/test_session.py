"""Tests for the Tion cloud objects bound to Home Assistant."""

from collections.abc import Callable
from typing import Any

from aiohttp import ClientSession
import pytest

from custom_components.tion import session
from custom_components.tion.api.auth import TionAuth, TionTokens
from custom_components.tion.api.device_key import TionDeviceKey
from custom_components.tion.api.transport import TionTransport, create_ssl_context

TOKENS = TionTokens(
    access_token="access",
    renew_session_token="renew",
    access_expires_at=1_000.0,
    refresh_expires_at=2_000.0,
)


class FakeHass:
    """The parts of hass the session helpers use."""

    def __init__(self) -> None:
        """Initialize the fake."""
        self.executor_jobs: list[Callable[[], Any]] = []

    async def async_add_executor_job(self, target: Callable[[], Any]) -> Any:
        """Record and run a blocking job."""
        self.executor_jobs.append(target)
        return target()


@pytest.fixture
def shared_session(monkeypatch: pytest.MonkeyPatch) -> ClientSession:
    """Stand in for Home Assistant's shared HTTP client."""
    fake = object.__new__(ClientSession)
    monkeypatch.setattr(session, "async_get_clientsession", lambda hass: fake)
    return fake


@pytest.mark.asyncio
async def test_create_transport_uses_shared_session(
    shared_session: ClientSession,
) -> None:
    """The transport rides on HA's client and builds its SSL context off-loop."""
    hass = FakeHass()

    transport = await session.async_create_transport(hass)

    assert isinstance(transport, TionTransport)
    assert transport.session is shared_session
    assert transport.ssl_context is create_ssl_context()
    assert hass.executor_jobs == [create_ssl_context]


@pytest.mark.asyncio
@pytest.mark.usefixtures("shared_session")
async def test_create_auth_binds_key_and_tokens() -> None:
    """The session helper builds a TionAuth for the given key and tokens."""
    key = TionDeviceKey.generate()

    auth = await session.async_create_auth(FakeHass(), key, TOKENS)

    assert isinstance(auth, TionAuth)
    assert auth.device_key is key
    assert auth.tokens == TOKENS
