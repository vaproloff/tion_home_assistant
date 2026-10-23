"""Tests for the account snapshot coordinator."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from ha_tests.common import MockConfigEntry, async_fire_time_changed
import pytest

from custom_components.tion.api import (
    TionApiError,
    TionAuthError,
    TionAuthStaleError,
    TionConnectionError,
)
from custom_components.tion.const import DOMAIN, REAUTH_STEP_STALE
from custom_components.tion.coordinator import TionCoordinator
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD
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


def _reauth_flows(hass: HomeAssistant) -> list[str]:
    return [
        flow["flow_id"]
        for flow in hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, match_context={"source": SOURCE_REAUTH}
        )
    ]


def _reauth_steps(hass: HomeAssistant) -> list[str]:
    return [
        flow["step_id"]
        for flow in hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, match_context={"source": SOURCE_REAUTH}
        )
    ]


async def test_refresh_auth_error_asks_to_sign_in_and_keeps_refreshing(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    coordinator: TionCoordinator,
    cloud: FakeTionCloud,
) -> None:
    """A terminal renew opens reauth as UpdateFailed; the next refresh still runs."""
    cloud.refresh_error = TionAuthError("expired")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.translation_key == "auth_failed"
    assert len(_reauth_flows(hass)) == 1

    await _tick(hass, freezer, 61)

    assert cloud.refreshes == 2


async def test_refresh_success_resolves_reauth(
    hass: HomeAssistant, coordinator: TionCoordinator, cloud: FakeTionCloud
) -> None:
    """The first successful refresh after a terminal renew closes the reauth flow."""
    cloud.refresh_error = TionAuthError("expired")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    cloud.refresh_error = None

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert _reauth_flows(hass) == []


async def test_refresh_success_keeps_reauth_while_cloud_auth_error(
    hass: HomeAssistant, coordinator: TionCoordinator, cloud: FakeTionCloud
) -> None:
    """Guard: a refresh without a renewal proves nothing while auth_error stands."""
    cloud.auth_error = TionAuthError("expired")
    cloud.push(cloud.account)
    await hass.async_block_till_done()

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        pytest.param(TionConnectionError("down"), "cloud_unavailable", id="connection"),
        pytest.param(TionApiError("bad"), "cloud_error", id="api"),
    ],
)
async def test_refresh_failure_with_auth_error_opens_and_closes_reauth(
    hass: HomeAssistant,
    coordinator: TionCoordinator,
    cloud: FakeTionCloud,
    error: Exception,
    translation_key: str,
) -> None:
    """A failed refresh still reports a flagged session; clearing it closes it."""
    cloud.auth_error = TionAuthStaleError("RenewAccess has been failing since 0")
    cloud.refresh_error = error

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.translation_key == translation_key
    assert _reauth_steps(hass) == [REAUTH_STEP_STALE]

    cloud.auth_error = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []


async def test_refresh_failure_without_auth_error_opens_nothing(
    hass: HomeAssistant, coordinator: TionCoordinator, cloud: FakeTionCloud
) -> None:
    """Guard: an ordinary outage is not a sign-in problem."""
    cloud.refresh_error = TionConnectionError("down")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


@pytest.mark.usefixtures("coordinator")
async def test_cloud_auth_error_opens_and_resolves_reauth(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """auth_error set opens reauth once; auth_error cleared closes it."""
    cloud.auth_error = TionAuthError("expired")
    cloud.push(cloud.account)
    cloud.push(cloud.account)
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1

    cloud.auth_error = None
    cloud.push(cloud.account)
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []


async def test_push_during_unload_does_not_open_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: FakeTionCloud
) -> None:
    """The old coordinator stays quiet while the entry reloads after a reauth."""
    cloud.auth_error = TionAuthError("expired")
    init_integration.mock_state(hass, ConfigEntryState.UNLOAD_IN_PROGRESS)

    cloud.push(cloud.account)
    await hass.async_block_till_done()

    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    init_integration.mock_state(hass, ConfigEntryState.LOADED)


@pytest.mark.usefixtures("coordinator")
async def test_pushes_without_auth_error_do_not_touch_flows(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """Guard: ordinary pushes neither open nor abort anything."""
    cloud.push(cloud.account)
    await hass.async_block_till_done()

    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


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


@pytest.mark.usefixtures("coordinator")
async def test_refusal_replaces_an_open_outage_form(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """An explicit refusal swaps the open "outage" form for the confirm form."""
    cloud.auth_error = TionAuthStaleError("RenewAccess has been failing since 0")
    cloud.push(cloud.account)
    await hass.async_block_till_done()
    assert _reauth_steps(hass) == [REAUTH_STEP_STALE]

    cloud.auth_error = TionAuthError("RenewAccess rejected (1): nope")
    cloud.push(cloud.account)
    await hass.async_block_till_done()

    assert _reauth_steps(hass) == ["reauth_confirm"]


@pytest.mark.usefixtures("coordinator")
async def test_refusal_keeps_a_flow_the_user_is_working_through(
    hass: HomeAssistant, cloud: FakeTionCloud
) -> None:
    """A refusal does not interrupt a flow already past the password form."""
    cloud.auth_error = TionAuthStaleError("RenewAccess has been failing since 0")
    cloud.push(cloud.account)
    await hass.async_block_till_done()
    assert _reauth_steps(hass) == [REAUTH_STEP_STALE]
    [flow_id] = _reauth_flows(hass)
    with (
        patch("custom_components.tion.config_flow.async_create_auth", AsyncMock()),
        patch("custom_components.tion.config_flow.async_register_captcha_view"),
    ):
        await hass.config_entries.flow.async_configure(flow_id, {CONF_PASSWORD: "pw"})

    cloud.auth_error = TionAuthError("RenewAccess rejected (1): nope")
    cloud.push(cloud.account)
    await hass.async_block_till_done()

    [flow] = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": SOURCE_REAUTH}
    )
    assert flow["flow_id"] == flow_id
    assert flow["step_id"] == "captcha"
