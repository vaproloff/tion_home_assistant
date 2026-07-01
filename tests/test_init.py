"""Tests for the Tion integration setup entry update listeners."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from custom_components import tion
from custom_components.tion.const import AUTH_DATA
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME


class FakeConfigEntries:
    """Fake Home Assistant config entries manager."""

    def __init__(self) -> None:
        """Initialize fake config entries manager."""
        self.updated_data: dict[str, Any] | None = None

    def async_update_entry(
        self, entry: FakeConfigEntry, *, data: dict[str, Any]
    ) -> None:
        """Record and apply updated entry data."""
        self.updated_data = data
        entry.data = data

    async def async_forward_entry_setups(
        self, entry: FakeConfigEntry, platforms: list[str]
    ) -> None:
        """Pretend platform setup succeeded."""


class FakeHass:
    """Fake Home Assistant object."""

    def __init__(self) -> None:
        """Initialize fake hass."""
        self.data: dict[str, Any] = {}
        self.config_entries = FakeConfigEntries()


class FakeConfigEntry:
    """Fake config entry."""

    def __init__(self, auth_data: str | dict[str, str | None] | None) -> None:
        """Initialize fake config entry."""
        self.entry_id = "entry-id"
        self.data: dict[str, Any] = {
            CONF_USERNAME: "user",
            CONF_PASSWORD: "pass",
            AUTH_DATA: auth_data,
        }
        self.options: dict[str, Any] = {}

    def async_on_unload(self, unload_callback: Callable[[], None]) -> None:
        """Pretend unload callback was registered."""

    def add_update_listener(
        self, listener: Callable[..., Awaitable[None]]
    ) -> Callable[[], None]:
        """Pretend update listener was registered."""
        return lambda: None


class FakeTionClient:
    """Fake Tion client capturing setup listeners."""

    instances: list[FakeTionClient] = []

    def __init__(
        self,
        session: object,
        username: str,
        password: str,
        *,
        min_update_interval_sec: int,
        auth: str | dict[str, str | None] | None,
    ) -> None:
        """Initialize fake client."""
        self.auth_listener: Callable[[str, str], Awaitable[None]] | None = None
        self.active_profile_listener: Callable[[str], Awaitable[None]] | None = None
        self.auth = auth
        self.instances.append(self)

    def add_update_listener(
        self, listener: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Capture the auth update listener."""
        self.auth_listener = listener

    def add_active_profile_listener(
        self, listener: Callable[[str], Awaitable[None]]
    ) -> None:
        """Capture the active profile update listener."""
        self.active_profile_listener = listener


class FakeCoordinator:
    """Fake data update coordinator."""

    def __init__(
        self,
        hass: FakeHass,
        entry: FakeConfigEntry,
        client: FakeTionClient,
        scan_interval: int,
    ) -> None:
        """Initialize fake coordinator."""
        self.pid_manager: FakePidManager | None = None

    async def async_config_entry_first_refresh(self) -> None:
        """Pretend initial refresh succeeded."""

    def get_devices(self) -> list[Any]:
        """Return no devices."""
        return []


class FakePidManager:
    """Fake PID manager."""

    def __init__(
        self, hass: FakeHass, entry: FakeConfigEntry, coordinator: FakeCoordinator
    ) -> None:
        """Initialize fake PID manager."""

    def async_start(self) -> Callable[[], None]:
        """Return a fake unload callback."""
        return lambda: None


def _patch_setup_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch setup dependencies that are irrelevant to auth merge behavior."""
    FakeTionClient.instances.clear()
    monkeypatch.setattr(tion, "TionClient", FakeTionClient)
    monkeypatch.setattr(tion, "TionDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(tion, "TionPidManager", FakePidManager)
    monkeypatch.setattr(tion, "async_create_clientsession", lambda hass: object())
    monkeypatch.setattr(tion.dr, "async_get", lambda hass: object())


@pytest.mark.parametrize(
    ("stored_auth", "expected_auth"),
    [
        pytest.param(
            "legacy-token",
            {"api": "new-token"},
            id="legacy_string_stored_is_replaced_without_crash",
        ),
        pytest.param(
            {"api": "old-token", "api2": "other-token"},
            {"api": "new-token", "api2": "other-token"},
            id="existing_dict_preserves_other_profile_token",
        ),
    ],
)
def test_setup_entry_auth_listener_merges_profile_token(
    monkeypatch: pytest.MonkeyPatch,
    stored_auth: str | dict[str, str | None],
    expected_auth: dict[str, str | None],
) -> None:
    """Auth update listener coerces stored auth and preserves other profile tokens."""
    _patch_setup_dependencies(monkeypatch)
    hass = FakeHass()
    entry = FakeConfigEntry(stored_auth)

    assert asyncio.run(tion.async_setup_entry(hass, entry)) is True
    client = FakeTionClient.instances[0]
    assert client.auth_listener is not None

    asyncio.run(client.auth_listener("api", "new-token"))

    assert hass.config_entries.updated_data is not None
    assert hass.config_entries.updated_data[AUTH_DATA] == expected_auth


def test_setup_entry_does_not_persist_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup must not wire active-profile persistence (no reload on failover)."""
    _patch_setup_dependencies(monkeypatch)
    hass = FakeHass()
    entry = FakeConfigEntry({"api": "token"})

    assert asyncio.run(tion.async_setup_entry(hass, entry)) is True

    client = FakeTionClient.instances[0]
    assert client.active_profile_listener is None
