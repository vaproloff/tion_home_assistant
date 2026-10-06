"""Tests for the account snapshot coordinator."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from ha_tests.common import MockConfigEntry, async_fire_time_changed
import pytest

from custom_components.tion.api import TionApiError, TionAuthError, TionConnectionError
from custom_components.tion.const import DOMAIN
from custom_components.tion.coordinator import TionCoordinator
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .fake_cloud import BREEZER_4S, FakeTionCloud, replace_device  # noqa: TID251


@pytest.fixture
def coordinator(init_integration: MockConfigEntry) -> TionCoordinator:
    """The coordinator of the loaded entry."""
    return init_integration.runtime_data


@pytest.fixture
def updates(coordinator: TionCoordinator) -> Iterator[list[bool]]:
    """Record channel_up at each listener call; a listener arms the refresh."""
    calls: list[bool] = []
    unsubscribe = coordinator.async_add_listener(
        lambda: calls.append(coordinator.channel_up)
    )
    yield calls
    unsubscribe()


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_push_updates_listeners(
    coordinator: TionCoordinator, cloud: FakeTionCloud, updates: list[bool]
) -> None:
    """A pushed snapshot reaches the listeners at once."""
    account = replace_device(cloud.account, BREEZER_4S, name="Renamed")

    cloud.push(account)

    assert coordinator.data is account
    assert updates == [True]


@pytest.mark.usefixtures("updates")
async def test_periodic_refresh_despite_pushes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, cloud: FakeTionCloud
) -> None:
    """Pushes every few seconds do not postpone the 60 s refresh."""
    for _ in range(6):
        await _tick(hass, freezer, 10)
        cloud.push(cloud.account)
    await _tick(hass, freezer, 1)

    assert cloud.refreshes == 1


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        pytest.param(TionConnectionError("down"), "cloud_unavailable", id="down"),
        pytest.param(TionApiError("bad"), "cloud_error", id="api"),
    ],
)
async def test_refresh_errors(
    coordinator: TionCoordinator,
    cloud: FakeTionCloud,
    error: Exception,
    translation_key: str,
) -> None:
    """A failed refresh is an UpdateFailed with a translated message."""
    cloud.refresh_error = error

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.translation_key == translation_key


async def test_refresh_auth_error_asks_to_sign_in(
    hass: HomeAssistant, coordinator: TionCoordinator, cloud: FakeTionCloud
) -> None:
    """A rejected sign-in during the refresh starts reauthentication."""
    cloud.refresh_error = TionAuthError("expired")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_channel_down_after_grace(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    coordinator: TionCoordinator,
    cloud: FakeTionCloud,
    updates: list[bool],
) -> None:
    """The channel counts as down only after 60 s without the live channel."""
    cloud.push(replace(cloud.account, connected=False))
    await _tick(hass, freezer, 59)
    assert all(updates)

    await _tick(hass, freezer, 2)
    assert not coordinator.channel_up
    assert updates[-1] is False

    cloud.push(replace(cloud.account, connected=True))
    assert coordinator.channel_up


async def test_reconnect_within_grace(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    coordinator: TionCoordinator,
    cloud: FakeTionCloud,
) -> None:
    """A short drop never takes the channel down."""
    cloud.push(replace(cloud.account, connected=False))
    await _tick(hass, freezer, 30)
    cloud.push(replace(cloud.account, connected=True))
    await _tick(hass, freezer, 60)

    assert coordinator.channel_up


async def test_unload_cancels_grace(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: FakeTionCloud
) -> None:
    """Unloading during a drop leaves no timer behind."""
    cloud.push(replace(cloud.account, connected=False))

    assert await hass.config_entries.async_unload(init_integration.entry_id)
