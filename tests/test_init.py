"""Tests for setting up and unloading a Tion account."""

from dataclasses import replace
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ha_tests.common import MockConfigEntry
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.tion import async_remove_config_entry_device
from custom_components.tion.api import (
    TionAccount,
    TionApiError,
    TionAuthError,
    TionConnectionError,
    TionTokens,
)
from custom_components.tion.api.views import BREEZER_PRODUCTS, STATION_PRODUCTS
from custom_components.tion.const import (
    CONF_DEVICE_KEY,
    CONF_PID_ENABLED,
    DOMAIN,
    MODEL_NAMES,
    PID_NUMBER_KEYS,
)
from custom_components.tion.coordinator import TionCoordinator
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .common import EMAIL, ENTRY_DATA, pid_options, setup_entry  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BREEZER_4S,
    BREEZER_O2,
    CLEVER,
    MAGICAIR,
    FakeAuth,
    FakeTionCloud,
    default_account,
    replace_device,
)


def _device(
    device_registry: dr.DeviceRegistry, entry: MockConfigEntry, device_id: str
) -> dr.DeviceEntry | None:
    return device_registry.async_get_device_by_identifier(
        (DOMAIN, device_id), entry.entry_id
    )


def _reauth_started(hass: HomeAssistant) -> bool:
    return any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: FakeTionCloud
) -> None:
    """The entry shares the cloud's snapshot and stops the cloud on unload."""
    assert init_integration.state is ConfigEntryState.LOADED
    coordinator = init_integration.runtime_data
    assert isinstance(coordinator, TionCoordinator)
    assert coordinator.data is cloud.account

    assert await hass.config_entries.async_unload(init_integration.entry_id)

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert cloud.stopped
    assert cloud.listeners == []


async def test_unload_unsubscribes_before_stopping(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
) -> None:
    """The disconnect caused by stopping never reaches the coordinator."""
    listeners_at_stop: list[int] = []
    stop = cloud.async_stop

    async def recording_stop() -> None:
        listeners_at_stop.append(len(cloud.listeners))
        await stop()

    cloud.async_stop = recording_stop
    await setup_entry(hass, config_entry, cloud, auth)

    assert await hass.config_entries.async_unload(config_entry.entry_id)

    assert listeners_at_stop == [0]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            {CONF_USERNAME: EMAIL, CONF_PASSWORD: "secret"}, id="before_v4_login"
        ),
        pytest.param({**ENTRY_DATA, CONF_DEVICE_KEY: "not a key"}, id="broken_key"),
        pytest.param(
            {key: value for key, value in ENTRY_DATA.items() if key != "access_token"},
            id="missing_token",
        ),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unusable_entry_asks_to_sign_in(
    hass: HomeAssistant,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    data: dict[str, Any],
) -> None:
    """An entry without a v4 sign-in starts reauthentication."""
    entry = MockConfigEntry(domain=DOMAIN, title=EMAIL, data=data)
    entry.add_to_hass(hass)

    await setup_entry(hass, entry, cloud, auth)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert _reauth_started(hass)


@pytest.mark.parametrize(
    ("error", "state", "reauth"),
    [
        pytest.param(
            TionAuthError("expired"), ConfigEntryState.SETUP_ERROR, True, id="auth"
        ),
        pytest.param(
            TionConnectionError("down"),
            ConfigEntryState.SETUP_RETRY,
            False,
            id="connection",
        ),
        pytest.param(
            TionApiError("bad"), ConfigEntryState.SETUP_RETRY, False, id="api"
        ),
    ],
)
async def test_start_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    error: Exception,
    state: ConfigEntryState,
    reauth: bool,
) -> None:
    """A rejected sign-in asks to sign in again; other errors retry later."""
    cloud.start_error = error

    await setup_entry(hass, config_entry, cloud, auth)

    assert config_entry.state is state
    assert _reauth_started(hass) is reauth
    assert auth.listeners == []


async def test_renewed_tokens_are_saved_without_reload(
    init_integration: MockConfigEntry, auth: FakeAuth
) -> None:
    """New tokens land in the entry data; the entry keeps running."""
    coordinator = init_integration.runtime_data
    tokens = TionTokens(
        access_token="new-access",
        renew_session_token="new-renew",
        access_expires_at=4_200_000_000.0,
        refresh_expires_at=4_300_000_000.0,
    )

    auth.renew(tokens)

    assert init_integration.data == {**ENTRY_DATA, **tokens.as_dict()}
    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.runtime_data is coordinator


async def test_background_auth_error_asks_to_sign_in(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: FakeTionCloud
) -> None:
    """The cloud giving up on a rejected sign-in starts reauthentication."""
    cloud.auth_error = TionAuthError("renew session expired")
    cloud.push(cloud.account)
    await hass.async_block_till_done()

    assert _reauth_started(hass)


@pytest.mark.usefixtures("init_integration")
async def test_device_registry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Supported devices are registered with their model and room."""
    devices = dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)

    assert sorted(devices, key=lambda device: device.name or "") == snapshot
    assert _device(device_registry, config_entry, CLEVER) is None
    bedroom_breezer = _device(device_registry, config_entry, BREEZER_4S)
    kitchen_breezer = _device(device_registry, config_entry, BREEZER_O2)
    assert bedroom_breezer is not None and kitchen_breezer is not None
    assert bedroom_breezer.area_id == area_registry.async_get_area_by_name("Bedroom").id
    assert kitchen_breezer.area_id is None


async def test_remove_only_devices_gone_from_the_account(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    cloud: FakeTionCloud,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A device can be removed once the account no longer has it."""
    device = _device(device_registry, init_integration, BREEZER_O2)
    assert device is not None

    assert not await async_remove_config_entry_device(hass, init_integration, device)

    cloud.push(replace_device(cloud.account, BREEZER_O2, id="GONE000001"))
    assert await async_remove_config_entry_device(hass, init_integration, device)


@pytest.mark.parametrize(
    "identifiers",
    [
        pytest.param({(DOMAIN, BREEZER_O2)}, id="tion-device"),
        pytest.param({("other", "device")}, id="other-integration-device"),
    ],
)
@pytest.mark.parametrize(
    ("error", "state"),
    [
        pytest.param(
            TionConnectionError("down"), ConfigEntryState.SETUP_RETRY, id="retry"
        ),
        pytest.param(TionAuthError("bad"), ConfigEntryState.SETUP_ERROR, id="error"),
    ],
)
async def test_device_is_kept_while_entry_is_not_loaded(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    device_registry: dr.DeviceRegistry,
    identifiers: set[tuple[str, str]],
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """Without a running entry the account is unknown, so nothing is removable."""
    cloud.start_error = error
    await setup_entry(hass, config_entry, cloud, auth)
    assert config_entry.state is state
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers=identifiers
    )

    assert not await async_remove_config_entry_device(hass, config_entry, device)


PID_NUMBERS_4S = {f"{BREEZER_4S}_{key}" for key in PID_NUMBER_KEYS}
KEPT_NUMBERS = {f"{MAGICAIR}_min_speed_set", "GONE000001_min_speed_set"}


@pytest.mark.parametrize(
    ("options", "remaining"),
    [
        pytest.param({}, KEPT_NUMBERS, id="without_pid"),
        pytest.param(
            pid_options(**{CONF_PID_ENABLED: False}), KEPT_NUMBERS, id="pid_disabled"
        ),
        pytest.param(pid_options(), KEPT_NUMBERS | PID_NUMBERS_4S, id="pid_set_up"),
    ],
)
async def test_pid_numbers_of_breezers_without_pid_are_removed(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    remaining: set[str],
) -> None:
    """Setup drops PID numbers of breezers without PID; others stay."""
    for unique_id in PID_NUMBERS_4S | KEPT_NUMBERS:
        entity_registry.async_get_or_create(
            Platform.NUMBER, DOMAIN, unique_id, config_entry=config_entry
        )

    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, config_entry, cloud, auth)

    assert {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        )
    } == remaining


def test_every_supported_model_has_a_name() -> None:
    """Every product the integration supports has a model name."""
    assert set(MODEL_NAMES) == BREEZER_PRODUCTS | STATION_PRODUCTS


async def test_conflicting_registry_device_does_not_fail_the_account(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A device that cannot be registered is skipped; the others still load."""
    mac = cloud.account.device(BREEZER_4S).macs[0]
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, BREEZER_4S)},
    )
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("other", "device")},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
    )

    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, config_entry, cloud, auth)

    assert config_entry.state is ConfigEntryState.LOADED
    assert _device(device_registry, config_entry, MAGICAIR) is not None
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and "Could not register" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "Breezer 4S" in warnings[0]
    assert BREEZER_4S not in caplog.text
    assert mac not in caplog.text


TRANSLATIONS = Path(__file__).parents[1] / "custom_components/tion/translations"


def _not_moved(account: TionAccount) -> TionAccount:
    return replace(
        account,
        locations=tuple(
            replace(location, needs_migration=True) for location in account.locations
        ),
    )


def _issue_id(entry: MockConfigEntry) -> str:
    return f"account_not_migrated_{entry.entry_id}"


@pytest.mark.parametrize("account", [_not_moved(default_account())])
async def test_account_not_moved_raises_an_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An account still on the old cloud loads and asks to move it in the app."""
    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, config_entry, cloud, auth)

    assert config_entry.state is ConfigEntryState.LOADED
    issue = issue_registry.async_get_issue(DOMAIN, _issue_id(config_entry))
    assert issue is not None
    assert issue.translation_key == "account_not_migrated"
    assert issue.translation_placeholders == {"email": EMAIL}
    assert issue.severity is ir.IssueSeverity.ERROR
    assert not issue.is_fixable


async def test_moved_account_clears_the_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Once the account is moved, the next load removes the issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(config_entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="account_not_migrated",
        translation_placeholders={"email": EMAIL},
    )

    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, config_entry, cloud, auth)

    assert issue_registry.async_get_issue(DOMAIN, _issue_id(config_entry)) is None


@pytest.mark.parametrize("account", [_not_moved(default_account())])
async def test_removing_the_entry_clears_the_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    cloud: FakeTionCloud,
    auth: FakeAuth,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Removing the entry removes its issue."""
    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, config_entry, cloud, auth)
    assert issue_registry.async_get_issue(DOMAIN, _issue_id(config_entry)) is not None

    await hass.config_entries.async_remove(config_entry.entry_id)

    assert issue_registry.async_get_issue(DOMAIN, _issue_id(config_entry)) is None


@pytest.mark.parametrize("language", ["en", "ru"])
def test_translations_cover_the_issue_and_reauth(language: str) -> None:
    """The issue and the re-login text are translated with their placeholders."""
    strings = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))

    issue = strings["issues"]["account_not_migrated"]
    assert "{" not in issue["title"]
    assert "{email}" in issue["description"]
    assert "{email}" in strings["config"]["step"]["reauth_confirm"]["description"]


@pytest.mark.parametrize("language", ["en", "ru"])
def test_translations_cover_the_device_error(language: str) -> None:
    """The device timeout error is translated and names the device."""
    strings = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))

    assert "{device}" in strings["exceptions"]["device_not_responding"]["message"]
