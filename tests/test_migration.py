"""Tests for moving a setup from before the v4 cloud to the current device IDs."""

import logging
from typing import Any
from unittest.mock import patch

from ha_tests.common import MockConfigEntry, mock_restore_cache_with_extra_data
import pytest

from custom_components.tion.api import AutoControl, TionAccount
from custom_components.tion.const import (
    CONF_CO2_SENSOR_ENTITY_ID,
    CONF_PID_BREEZERS,
    CONF_PID_ENABLED,
    CONF_PID_KP,
    CONF_PID_MAX_SPEED,
    CONF_PID_MIN_SPEED,
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    CONF_PRESETS,
    DOMAIN,
)
from homeassistant.components.climate import ATTR_FAN_MODE, ATTR_PRESET_MODE
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .common import PID_SENSOR, entity_id, pid_options, setup_entry  # noqa: TID251
from .fake_cloud import (  # noqa: TID251
    BEDROOM_ID,
    BREEZER_3S,
    BREEZER_4S,
    BREEZER_O2,
    MAGICAIR,
    FakeAuth,
    FakeTionCloud,
    default_account,
    replace_device,
    set_room_auto,
)

OLD_4S = "11111111-1111-4111-8111-111111111111"
OLD_O2 = "22222222-2222-4222-8222-222222222222"
OLD_3S = "33333333-3333-4333-8333-333333333333"
OLD_MAGICAIR = "44444444-4444-4444-8444-444444444444"
CURRENT_IDS = {
    OLD_4S: BREEZER_4S,
    OLD_O2: BREEZER_O2,
    OLD_3S: BREEZER_3S,
    OLD_MAGICAIR: MAGICAIR,
}
ROUTER_IDENTIFIER = ("router", "client-1")
OLD_PID = {
    CONF_PID_ENABLED: True,
    CONF_CO2_SENSOR_ENTITY_ID: PID_SENSOR,
    CONF_PID_KP: 0.7,
}
OLD_OPTIONS = {
    CONF_SCAN_INTERVAL: 30,
    CONF_PID_BREEZERS: {OLD_4S: OLD_PID},
    CONF_PRESETS: {
        OLD_4S: {
            "eco": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 2},
            "sleep": {
                CONF_PRESET_TYPE: "auto",
                CONF_PRESET_MIN_SPEED: 1,
                CONF_PRESET_MAX_SPEED: 3,
            },
            "boost": {
                CONF_PRESET_TYPE: "auto",
                CONF_PRESET_MIN_SPEED: 7,
                CONF_PRESET_MAX_SPEED: 9,
            },
            "away": {CONF_PRESET_TYPE: "auto"},
        },
        OLD_3S: {
            "eco": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 1},
            "sleep": {
                CONF_PRESET_TYPE: "auto",
                CONF_PRESET_MIN_SPEED: 1,
                CONF_PRESET_MAX_SPEED: 2,
            },
        },
        OLD_O2: {
            "sleep": {
                CONF_PRESET_TYPE: "auto",
                CONF_PRESET_MIN_SPEED: 1,
                CONF_PRESET_MAX_SPEED: 2,
            },
        },
    },
}
RF_MAC = "00:00:aa:bb:cc:dd"


def _mac(account: TionAccount, device_id: str) -> str:
    device = account.device(device_id)
    assert device is not None
    return device.macs[0]


def _old_device(
    device_registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
    old_id: str,
    mac: str,
    *other: tuple[str, str],
) -> dr.DeviceEntry:
    return device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, old_id), *other},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
    )


def _old_entity(
    entity_registry: er.EntityRegistry,
    entry: MockConfigEntry,
    platform: Platform,
    unique_id: str,
    object_id: str,
    device: dr.DeviceEntry | None = None,
) -> str:
    return entity_registry.async_get_or_create(
        platform,
        DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=device.id if device is not None else None,
        suggested_object_id=object_id,
    ).entity_id


def _unique_ids(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry
) -> dict[str, str]:
    return {
        entity.entity_id: entity.unique_id
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }


def _identifiers(
    device_registry: dr.DeviceRegistry, device: dr.DeviceEntry
) -> set[tuple[str, str]]:
    current = device_registry.async_get(device.id)
    assert current is not None
    return current.identifiers


async def _load(
    hass: HomeAssistant, entry: MockConfigEntry, account: TionAccount
) -> None:
    """Load the entry without platforms on a cloud with this account."""
    with patch("custom_components.tion.PLATFORMS", []):
        await setup_entry(hass, entry, FakeTionCloud(account), FakeAuth())


@pytest.mark.parametrize("options", [{CONF_PID_BREEZERS: {OLD_4S: OLD_PID}}])
async def test_old_entities_move_to_the_current_ids(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Registry entries move to the current IDs; leftovers of the old model go."""
    breezer = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    station = _old_device(
        device_registry,
        config_entry,
        OLD_MAGICAIR,
        _mac(account, MAGICAIR),
        ROUTER_IDENTIFIER,
    )
    breezer_3s = _old_device(
        device_registry, config_entry, OLD_3S, _mac(account, BREEZER_3S)
    )
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", breezer
    )
    inflow = _old_entity(
        entity_registry,
        config_entry,
        Platform.SENSOR,
        f"{OLD_4S}_temperature_in",
        "old_4s_inflow",
        breezer,
    )
    pid_min = _old_entity(
        entity_registry,
        config_entry,
        Platform.NUMBER,
        f"{OLD_4S}_min_speed_set",
        "old_4s_min_speed",
        breezer,
    )
    auto_mode = _old_entity(
        entity_registry,
        config_entry,
        Platform.SWITCH,
        f"{OLD_MAGICAIR}_auto_mode",
        "old_auto_mode",
        station,
    )
    # A room number of a breezer without PID; the stale PID number cleanup drops it.
    _old_entity(
        entity_registry,
        config_entry,
        Platform.NUMBER,
        f"{OLD_3S}_min_speed_set",
        "old_3s_min_speed",
        breezer_3s,
    )
    _old_entity(
        entity_registry,
        config_entry,
        Platform.SENSOR,
        f"{config_entry.entry_id}_api_profile",
        "old_api_profile",
    )

    await _load(hass, config_entry, account)

    assert _unique_ids(entity_registry, config_entry) == {
        climate: BREEZER_4S,
        inflow: f"{BREEZER_4S}_temperature_in",
        pid_min: f"{BREEZER_4S}_min_speed_set",
        auto_mode: f"{MAGICAIR}_auto_mode",
    }
    assert _identifiers(device_registry, breezer) == {(DOMAIN, BREEZER_4S)}
    assert _identifiers(device_registry, station) == {
        (DOMAIN, MAGICAIR),
        ROUTER_IDENTIFIER,
    }


@pytest.mark.parametrize(
    ("account", "old_mac"),
    [
        pytest.param(
            default_account(),
            _mac(default_account(), BREEZER_4S),
            id="same_notation",
        ),
        pytest.param(
            replace_device(default_account(), BREEZER_4S, macs=(RF_MAC,)),
            "AA:BB:CC:DD",
            id="short_rf_notation",
        ),
    ],
)
async def test_mac_is_compared_by_value(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    old_mac: str,
) -> None:
    """An old MAC written differently still finds its device."""
    device = _old_device(device_registry, config_entry, OLD_4S, old_mac)
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )

    await _load(hass, config_entry, account)

    assert _unique_ids(entity_registry, config_entry) == {climate: BREEZER_4S}


@pytest.mark.parametrize("options", [OLD_OPTIONS])
async def test_old_options_move_to_the_current_format(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Options move to current IDs; auto presets become PID presets or go."""
    for old_id, current_id in CURRENT_IDS.items():
        _old_device(device_registry, config_entry, old_id, _mac(account, current_id))

    await _load(hass, config_entry, account)

    assert config_entry.options == {
        CONF_PID_BREEZERS: {
            BREEZER_4S: {**OLD_PID, CONF_PID_MIN_SPEED: 1, CONF_PID_MAX_SPEED: 4}
        },
        CONF_PRESETS: {
            BREEZER_4S: {
                "eco": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 2},
                "sleep": {
                    CONF_PRESET_TYPE: "local_pid",
                    CONF_PRESET_MIN_SPEED: 1,
                    CONF_PRESET_MAX_SPEED: 3,
                },
                "boost": {
                    CONF_PRESET_TYPE: "local_pid",
                    CONF_PRESET_MIN_SPEED: 6,
                    CONF_PRESET_MAX_SPEED: 6,
                },
            },
            BREEZER_3S: {"eco": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 1}},
        },
    }


@pytest.mark.parametrize(
    ("account", "options", "expected"),
    [
        pytest.param(
            default_account(),
            {CONF_PID_BREEZERS: {OLD_4S: OLD_PID}},
            {**OLD_PID, CONF_PID_MIN_SPEED: 1, CONF_PID_MAX_SPEED: 4},
            id="room_limits",
        ),
        pytest.param(
            set_room_auto(
                default_account(),
                BEDROOM_ID,
                AutoControl(enabled=False, speed_min=7, speed_max=9, co2_target=800),
            ),
            {CONF_PID_BREEZERS: {OLD_4S: OLD_PID}},
            {**OLD_PID, CONF_PID_MIN_SPEED: 6, CONF_PID_MAX_SPEED: 6},
            id="above_breezer_max",
        ),
        pytest.param(
            set_room_auto(default_account(), BEDROOM_ID, None),
            {CONF_PID_BREEZERS: {OLD_4S: OLD_PID}},
            OLD_PID,
            id="no_room_auto",
        ),
        pytest.param(
            default_account(),
            {
                CONF_PID_BREEZERS: {
                    OLD_4S: {**OLD_PID, CONF_PID_MIN_SPEED: 2, CONF_PID_MAX_SPEED: 5}
                }
            },
            {**OLD_PID, CONF_PID_MIN_SPEED: 2, CONF_PID_MAX_SPEED: 5},
            id="limits_kept",
        ),
    ],
)
async def test_pid_limits_come_from_the_room(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    expected: dict[str, Any],
) -> None:
    """A moved PID breezer gets the limits the old PID used: its room's."""
    _old_device(device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S))

    await _load(hass, config_entry, account)

    assert config_entry.options[CONF_PID_BREEZERS] == {BREEZER_4S: expected}


@pytest.mark.parametrize("options", [OLD_OPTIONS])
async def test_second_load_changes_nothing(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Once moved, a reload or a restart writes nothing."""
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )
    await _load(hass, config_entry, account)
    moved = _unique_ids(entity_registry, config_entry)
    options = dict(config_entry.options)
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as update_entry:
        await _load(hass, config_entry, account)

    update_entry.assert_not_called()
    assert _unique_ids(entity_registry, config_entry) == moved
    assert config_entry.options == options


async def test_taken_unique_id_leaves_both_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A leftover entity on the current unique ID does not break loading."""
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    old = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )
    current = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, BREEZER_4S, "current_4s"
    )

    await _load(hass, config_entry, account)

    assert config_entry.state is ConfigEntryState.LOADED
    assert _unique_ids(entity_registry, config_entry) == {
        old: OLD_4S,
        current: BREEZER_4S,
    }


async def test_device_gone_from_the_account_is_untouched(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A registered device whose MAC the account lacks keeps its old ID."""
    device = _old_device(device_registry, config_entry, OLD_4S, "aa:bb:cc:dd:ee:ff")
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "gone_4s", device
    )

    await _load(hass, config_entry, account)

    assert _identifiers(device_registry, device) == {(DOMAIN, OLD_4S)}
    assert _unique_ids(entity_registry, config_entry) == {climate: OLD_4S}


async def test_moved_climate_keeps_its_entity_id(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """With all platforms, the moved climate comes up under its old entity ID."""
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )

    await setup_entry(hass, config_entry, FakeTionCloud(account), FakeAuth())

    assert entity_id(hass, Platform.CLIMATE, BREEZER_4S) == climate
    state = hass.states.get(climate)
    assert state is not None
    assert not state.attributes.get("restored")
    assert not [
        moved
        for moved in _unique_ids(entity_registry, config_entry)
        if moved.endswith("_2")
    ]


SHARED_MAC_ACCOUNT = replace_device(
    default_account(), MAGICAIR, macs=(_mac(default_account(), BREEZER_4S),)
)


def _device_identifiers(
    device_registry: dr.DeviceRegistry, entry: MockConfigEntry
) -> dict[str, set[tuple[str, str]]]:
    return {
        device.id: device.identifiers
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    }


@pytest.mark.parametrize("account", [pytest.param(SHARED_MAC_ACCOUNT, id="shared_mac")])
async def test_current_devices_are_never_moved(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """On a v4 setup, devices sharing a MAC value keep their IDs on a reload."""
    await setup_entry(hass, config_entry, FakeTionCloud(account), FakeAuth())
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    unique_ids = _unique_ids(entity_registry, config_entry)
    identifiers = _device_identifiers(device_registry, config_entry)

    await setup_entry(hass, config_entry, FakeTionCloud(account), FakeAuth())

    assert _unique_ids(entity_registry, config_entry) == unique_ids
    assert _device_identifiers(device_registry, config_entry) == identifiers


@pytest.mark.parametrize("account", [pytest.param(SHARED_MAC_ACCOUNT, id="shared_mac")])
async def test_ambiguous_mac_matches_nothing(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An old device whose MAC two account devices share stays as it was."""
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(default_account(), BREEZER_4S)
    )
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )

    await _load(hass, config_entry, account)

    assert (DOMAIN, OLD_4S) in _identifiers(device_registry, device)
    assert _unique_ids(entity_registry, config_entry) == {climate: OLD_4S}


async def test_unmatched_old_device_logs_only_mac_shapes(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log tells how many digits the MACs have, never the MACs or IDs."""
    caplog.set_level(logging.DEBUG, logger="custom_components.tion")
    _old_device(device_registry, config_entry, OLD_4S, "aa:bb:cc:dd:ee:ff")

    await _load(hass, config_entry, account)

    assert "registered MAC digits [12], account MAC digits [12]" in caplog.text
    for secret in (OLD_4S, "aa:bb:cc:dd:ee:ff", _mac(account, BREEZER_4S)):
        assert secret not in caplog.text


async def test_moving_is_logged_once(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A load that moved something says how much; the next one stays quiet."""
    caplog.set_level(logging.INFO, logger="custom_components.tion")
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )

    await _load(hass, config_entry, account)

    assert "Moved 1 Tion devices and 1 entities to the new cloud IDs" in caplog.text
    caplog.clear()
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    await _load(hass, config_entry, account)

    assert "Moved" not in caplog.text


@pytest.mark.parametrize(
    "account",
    [
        pytest.param(
            replace_device(default_account(), BREEZER_4S, macs=(RF_MAC,)),
            id="short_rf_notation",
        )
    ],
)
async def test_taken_current_identifier_skips_the_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An old device is left alone when another device has the current ID."""
    device = _old_device(device_registry, config_entry, OLD_4S, "AA:BB:CC:DD")
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, BREEZER_4S)},
        connections={(dr.CONNECTION_NETWORK_MAC, RF_MAC)},
    )
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )

    await _load(hass, config_entry, account)

    assert config_entry.state is ConfigEntryState.LOADED
    assert _identifiers(device_registry, device) == {(DOMAIN, OLD_4S)}
    assert _unique_ids(entity_registry, config_entry) == {climate: OLD_4S}


@pytest.mark.parametrize(
    "account",
    [
        pytest.param(
            set_room_auto(
                replace_device(default_account(), BREEZER_O2, room_id=BEDROOM_ID),
                BEDROOM_ID,
                AutoControl(enabled=False, speed_min=3, speed_max=6, co2_target=800),
            ),
            id="o2_in_a_wide_room",
        )
    ],
)
@pytest.mark.parametrize(
    "options",
    [
        {
            CONF_PID_BREEZERS: {OLD_O2: OLD_PID},
            CONF_PRESETS: {
                OLD_O2: {
                    "sleep": {
                        CONF_PRESET_TYPE: "auto",
                        CONF_PRESET_MIN_SPEED: 3,
                        CONF_PRESET_MAX_SPEED: 6,
                    }
                }
            },
        }
    ],
)
async def test_limits_are_capped_at_the_breezer_maximum(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A breezer with a top speed below 6 gets limits and presets within it."""
    _old_device(device_registry, config_entry, OLD_O2, _mac(account, BREEZER_O2))

    await _load(hass, config_entry, account)

    assert config_entry.options == {
        CONF_PID_BREEZERS: {
            BREEZER_O2: {**OLD_PID, CONF_PID_MIN_SPEED: 3, CONF_PID_MAX_SPEED: 4}
        },
        CONF_PRESETS: {
            BREEZER_O2: {
                "sleep": {
                    CONF_PRESET_TYPE: "local_pid",
                    CONF_PRESET_MIN_SPEED: 3,
                    CONF_PRESET_MAX_SPEED: 4,
                }
            }
        },
    }


CURRENT_PID = {**OLD_PID, CONF_PID_KP: 0.2}
CURRENT_PRESETS = {"eco": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 1}}


@pytest.mark.parametrize(
    "options",
    [
        {
            CONF_PID_BREEZERS: {OLD_4S: OLD_PID, BREEZER_4S: CURRENT_PID},
            CONF_PRESETS: {
                OLD_4S: {"sleep": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 2}},
                BREEZER_4S: CURRENT_PRESETS,
            },
        }
    ],
)
async def test_current_key_wins_over_the_old_one(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Options already under the current ID stay as they are; the old ones go."""
    _old_device(device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S))

    await _load(hass, config_entry, account)

    assert config_entry.options == {
        CONF_PID_BREEZERS: {BREEZER_4S: CURRENT_PID},
        CONF_PRESETS: {BREEZER_4S: CURRENT_PRESETS},
    }


@pytest.mark.parametrize(
    "options",
    [
        {
            **pid_options(OLD_4S),
            CONF_PRESETS: {
                OLD_4S: {"sleep": {CONF_PRESET_TYPE: "manual", CONF_PRESET_SPEED: 1}}
            },
        }
    ],
)
async def test_old_climate_restore_payload_resumes_pid(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A payload of the old release resumes PID on the moved climate, no preset."""
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )
    hass.states.async_set(PID_SENSOR, "1200")
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(climate, "heat"),
                {
                    "pid_active": True,
                    "preset_mode": "sleep",
                    "preset_saved": {"fan_speed": 3},
                },
            )
        ],
    )

    with patch("custom_components.tion.PLATFORMS", [Platform.CLIMATE]):
        await setup_entry(hass, config_entry, FakeTionCloud(account), FakeAuth())

    state = hass.states.get(climate)
    assert state is not None
    assert (
        state.attributes[ATTR_FAN_MODE],
        state.attributes["pid_active"],
        state.attributes[ATTR_PRESET_MODE],
    ) == ("local_pid", True, "none")


@pytest.mark.parametrize(
    "options",
    [
        {
            CONF_PID_BREEZERS: {OLD_4S: OLD_PID},
            CONF_PRESETS: {OLD_4S: CURRENT_PRESETS},
        }
    ],
)
async def test_stub_id_moves_on_to_the_real_one(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    account: TionAccount,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Old ID to stub ID, then stub ID to the real one once the account is updated."""
    stub = "STUB000001"
    device = _old_device(
        device_registry, config_entry, OLD_4S, _mac(account, BREEZER_4S)
    )
    climate = _old_entity(
        entity_registry, config_entry, Platform.CLIMATE, OLD_4S, "old_4s", device
    )
    inflow = _old_entity(
        entity_registry,
        config_entry,
        Platform.SENSOR,
        f"{OLD_4S}_temperature_in",
        "old_4s_inflow",
        device,
    )

    await _load(hass, config_entry, replace_device(account, BREEZER_4S, id=stub))

    assert _identifiers(device_registry, device) == {(DOMAIN, stub)}
    assert _unique_ids(entity_registry, config_entry) == {
        climate: stub,
        inflow: f"{stub}_temperature_in",
    }
    assert set(config_entry.options[CONF_PID_BREEZERS]) == {stub}
    assert set(config_entry.options[CONF_PRESETS]) == {stub}
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    await _load(hass, config_entry, account)

    assert _identifiers(device_registry, device) == {(DOMAIN, BREEZER_4S)}
    assert _unique_ids(entity_registry, config_entry) == {
        climate: BREEZER_4S,
        inflow: f"{BREEZER_4S}_temperature_in",
    }
    assert set(config_entry.options[CONF_PID_BREEZERS]) == {BREEZER_4S}
    assert set(config_entry.options[CONF_PRESETS]) == {BREEZER_4S}
