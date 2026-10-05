"""Fixtures and constants for the Home Assistant layer tests."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

from ha_tests.common import MockConfigEntry
import pytest

from custom_components.tion.api import TionAccount, TionDeviceKey, TionTokens
from custom_components.tion.const import (
    CONF_DEVICE_KEY,
    CONF_DEVICE_KEY_ID,
    DOMAIN,
    PLATFORMS,
)
from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .fake_cloud import FakeAuth, FakeTionCloud, default_account  # noqa: TID251

EMAIL = "user@example.com"
UNIQUE_ID = "tion-account"
DEVICE_KEY = TionDeviceKey.generate()
TOKENS = TionTokens(
    access_token="access",
    renew_session_token="renew",
    access_expires_at=4_000_000_000.0,
    refresh_expires_at=4_100_000_000.0,
)
ENTRY_DATA = {
    CONF_EMAIL: EMAIL,
    CONF_DEVICE_KEY: DEVICE_KEY.to_pem(),
    CONF_DEVICE_KEY_ID: DEVICE_KEY.key_id(),
    **TOKENS.as_dict(),
}


@pytest.fixture
def account() -> TionAccount:
    """The account the cloud starts with; tests parametrize it to change it."""
    return default_account()


@pytest.fixture
def cloud(account: TionAccount) -> FakeTionCloud:
    """The cloud the integration gets."""
    return FakeTionCloud(account)


@pytest.fixture
def auth() -> FakeAuth:
    """The session the integration gets."""
    return FakeAuth()


@pytest.fixture
def config_entry(
    hass: HomeAssistant, enable_custom_integrations: None
) -> MockConfigEntry:
    """A config entry made by the v4 login."""
    entry = MockConfigEntry(
        domain=DOMAIN, title=EMAIL, unique_id=UNIQUE_ID, data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
) -> None:
    """Set up the config entry on the fake cloud."""
    with patch(
        "custom_components.tion.async_create_cloud",
        AsyncMock(return_value=(auth, cloud)),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
def platforms() -> list[Platform]:
    """The platforms to set up; a platform's tests narrow it to their own."""
    return PLATFORMS


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    platforms: list[Platform],
) -> AsyncIterator[MockConfigEntry]:
    """Set up the config entry on the fake cloud."""
    with patch("custom_components.tion.PLATFORMS", platforms):
        await setup_entry(hass, config_entry, cloud, auth)
        yield config_entry


def entity_id(hass: HomeAssistant, platform: Platform, unique_id: str) -> str:
    """Return the entity id of an entity of the integration by its unique id."""
    entity = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity is not None, unique_id
    return entity
