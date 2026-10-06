"""The Tion integration."""

from collections.abc import Mapping
import logging
from string import hexdigits
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .api import (
    Breezer,
    TionAccount,
    TionAuthError,
    TionConnectionError,
    TionDeviceKey,
    TionError,
    TionTokens,
    view,
)
from .const import (
    CONF_DEVICE_KEY,
    CONF_PID_BREEZERS,
    CONF_PID_MAX_SPEED,
    CONF_PID_MIN_SPEED,
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_TYPE,
    CONF_PRESETS,
    DOMAIN,
    MANUFACTURER,
    MODEL_NAMES,
    PID_NUMBER_KEYS,
    PLATFORMS,
    TionPresetType,
)
from .coordinator import TionConfigEntry, TionCoordinator
from .pid_manager import TionPidManager, breezer_speed_max, is_pid_set_up
from .session import async_create_cloud

_LOGGER = logging.getLogger(__name__)

# The old cloud's room auto preset; v4 has no such preset type.
_OLD_PRESET_AUTO = "auto"


async def async_setup_entry(hass: HomeAssistant, entry: TionConfigEntry) -> bool:
    """Set up a Tion account from a config entry."""
    try:
        device_key = TionDeviceKey.from_pem(entry.data[CONF_DEVICE_KEY])
        tokens = TionTokens.from_dict(entry.data)
    except (KeyError, TypeError, ValueError) as err:
        # Entries from before the v4 login hold a username and a password.
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err

    auth, cloud = await async_create_cloud(
        hass,
        device_key,
        tokens,
        lambda coro, name: entry.async_create_background_task(hass, coro, name),
    )

    @callback
    def _save_tokens(new_tokens: TionTokens) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **new_tokens.as_dict()}
        )

    entry.async_on_unload(auth.add_update_listener(_save_tokens))

    try:
        await cloud.async_start()
    except TionAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except TionConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cloud_unavailable"
        ) from err
    except TionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cloud_error",
            translation_placeholders={"message": str(err)},
        ) from err
    # Unload callbacks run last-in first-out: the coordinator, registered
    # below, stops listening before the cloud stops.
    entry.async_on_unload(cloud.async_stop)

    account = cloud.account
    _migrate_options(hass, entry, account, _migrate_old_ids(hass, entry, account))
    _update_migration_issue(hass, entry, account)
    coordinator = entry.runtime_data = TionCoordinator(hass, entry, cloud)
    coordinator.pid = TionPidManager(hass, entry, coordinator)
    entry.async_on_unload(coordinator.pid.async_stop)
    _register_devices(hass, entry, account)
    _remove_stale_pid_numbers(hass, entry, coordinator.pid)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TionConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: TionConfigEntry) -> None:
    """Drop the entry's repair issue."""
    ir.async_delete_issue(hass, DOMAIN, _migration_issue_id(entry))


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TionConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device the account no longer has."""
    if entry.state is not ConfigEntryState.LOADED:
        return False
    account = entry.runtime_data.data
    return not any(
        domain == DOMAIN and account.device(device_id) is not None
        for domain, device_id in device_entry.identifiers
    )


def _remove_stale_pid_numbers(
    hass: HomeAssistant, entry: TionConfigEntry, pid: TionPidManager
) -> None:
    """Remove the PID numbers of breezers that no longer have local PID set up."""
    stale = {
        f"{device.id}_{key}"
        for device in entry.runtime_data.data.devices()
        if isinstance(view(device), Breezer) and not pid.is_configured(device.id)
        for key in PID_NUMBER_KEYS
    }
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == Platform.NUMBER and entity.unique_id in stale:
            registry.async_remove(entity.entity_id)


def _migrate_old_ids(
    hass: HomeAssistant, entry: TionConfigEntry, account: TionAccount
) -> dict[str, str]:
    """Move devices and entities known by an old ID to the current one.

    Setups from before the v4 cloud know devices by the old cloud's IDs; the
    MAC, the same in both clouds, ties them to the current ones. Returns the
    moves, old ID to current ID.
    """
    current = _current_ids_by_mac(account)
    device_registry = dr.async_get(hass)
    moves: dict[str, str] = {}
    moved_devices = moved_entities = 0
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        old = {
            identifier
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN and account.device(identifier[1]) is None
        }
        if not old:
            continue
        if (current_id := _current_id(device_entry, current)) is None:
            _LOGGER.debug(
                "No Tion device in the account matches a %s"
                " (registered MAC digits %s, account MAC digits %s)",
                device_entry.model,
                sorted(_mac_digits(mac) for mac in _device_macs(device_entry)),
                sorted(
                    {
                        _mac_digits(mac)
                        for device in account.devices()
                        for mac in device.macs
                    }
                ),
            )
            continue
        try:
            device_registry.async_update_device(
                device_entry.id,
                new_identifiers=(device_entry.identifiers - old)
                | {(DOMAIN, current_id)},
            )
        except dr.DeviceIdentifierCollisionError:
            _LOGGER.debug(
                "Another device already has the current ID of a %s", device_entry.model
            )
            continue
        moved_devices += 1
        moves.update((old_id, current_id) for _, old_id in old)

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.unique_id == f"{entry.entry_id}_api_profile":
            entity_registry.async_remove(entity.entity_id)
        elif (unique_id := _moved_unique_id(entity.unique_id, moves)) is not None:
            try:
                entity_registry.async_update_entity(
                    entity.entity_id, new_unique_id=unique_id
                )
            except ValueError:
                _LOGGER.debug(
                    "The current unique ID of a %s entity is taken", entity.domain
                )
            else:
                moved_entities += 1
    if moved_devices or moved_entities:
        _LOGGER.info(
            "Moved %d Tion devices and %d entities to the new cloud IDs",
            moved_devices,
            moved_entities,
        )
    return moves


def _mac_value(mac: str) -> int | None:
    """Return a MAC as a number, whatever its separators, case and padding."""
    digits = "".join(char for char in mac if char in hexdigits)
    if not digits:
        return None
    return int(digits, 16) or None


def _mac_digits(mac: str) -> int:
    return sum(char in hexdigits for char in mac)


def _device_macs(device_entry: dr.DeviceEntry) -> list[str]:
    return [
        mac
        for kind, mac in device_entry.connections
        if kind == dr.CONNECTION_NETWORK_MAC
    ]


def _current_ids_by_mac(account: TionAccount) -> dict[int, str]:
    """Map MAC values to device IDs; a value of several devices is left out."""
    owners: dict[int, set[str]] = {}
    for device in account.devices():
        for mac in device.macs:
            if (value := _mac_value(mac)) is not None:
                owners.setdefault(value, set()).add(device.id)
    return {value: next(iter(ids)) for value, ids in owners.items() if len(ids) == 1}


def _current_id(device_entry: dr.DeviceEntry, current: Mapping[int, str]) -> str | None:
    """Return the current ID of a registered device, found by its MAC."""
    for mac in _device_macs(device_entry):
        if (value := _mac_value(mac)) is not None and value in current:
            return current[value]
    return None


def _moved_unique_id(unique_id: str, moves: Mapping[str, str]) -> str | None:
    """Return the unique ID under the device's current ID; None if not moved."""
    # Prefix match: current IDs may contain "_", so the ID is not split off.
    for old_id, current_id in moves.items():
        if unique_id == old_id:
            return current_id
        if unique_id.startswith(f"{old_id}_"):
            return current_id + unique_id.removeprefix(old_id)
    return None


def _migrate_options(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    account: TionAccount,
    moves: Mapping[str, str],
) -> None:
    """Bring options of setups from before the v4 cloud to the current format."""
    options = dict(entry.options)
    options.pop(CONF_SCAN_INTERVAL, None)
    pid_breezers, moved = _moved_keys(options.get(CONF_PID_BREEZERS, {}), moves)
    for breezer_id in moved:
        pid_breezers[breezer_id] = {
            **_room_pid_limits(account, breezer_id),
            **pid_breezers[breezer_id],
        }
    presets, _ = _moved_keys(options.get(CONF_PRESETS, {}), moves)
    for breezer_id, breezer_presets in list(presets.items()):
        kept = _without_auto_presets(
            breezer_presets,
            pid_set_up=is_pid_set_up(pid_breezers.get(breezer_id, {})),
            speed_max=breezer_speed_max(account, breezer_id),
        )
        if kept == breezer_presets:
            continue
        if kept:
            presets[breezer_id] = kept
        else:
            del presets[breezer_id]
    for key, value in ((CONF_PID_BREEZERS, pid_breezers), (CONF_PRESETS, presets)):
        if value:
            options[key] = value
        else:
            options.pop(key, None)
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)


def _moved_keys(
    values: Mapping[str, Any], moves: Mapping[str, str]
) -> tuple[dict[str, Any], set[str]]:
    """Return the values keyed by current IDs and the keys moved; current keys win."""
    result = {key: value for key, value in values.items() if key not in moves}
    moved: set[str] = set()
    for key, value in values.items():
        if (current_id := moves.get(key)) is not None and current_id not in result:
            result[current_id] = value
            moved.add(current_id)
    return result, moved


def _room_pid_limits(account: TionAccount, breezer_id: str) -> dict[str, int]:
    """Return PID limits from the breezer room's auto mode, as the old PID used."""
    device = account.device(breezer_id)
    room = account.room_of(device) if device is not None else None
    auto = room.configured_auto if room is not None else None
    if auto is None:
        return {}
    speed_max = min(auto.speed_max, breezer_speed_max(account, breezer_id))
    return {
        CONF_PID_MIN_SPEED: min(auto.speed_min, speed_max),
        CONF_PID_MAX_SPEED: speed_max,
    }


def _without_auto_presets(
    presets: Mapping[str, Any], *, pid_set_up: bool, speed_max: int
) -> dict[str, Any]:
    """Turn old auto presets into PID presets on a breezer with PID; drop the rest."""
    result: dict[str, Any] = {}
    for name, preset in presets.items():
        if preset.get(CONF_PRESET_TYPE) != _OLD_PRESET_AUTO:
            result[name] = preset
            continue
        min_speed = preset.get(CONF_PRESET_MIN_SPEED)
        max_speed = preset.get(CONF_PRESET_MAX_SPEED)
        if pid_set_up and isinstance(min_speed, int) and isinstance(max_speed, int):
            top = min(max_speed, speed_max)
            result[name] = {
                CONF_PRESET_TYPE: TionPresetType.LOCAL_PID.value,
                CONF_PRESET_MIN_SPEED: min(min_speed, top),
                CONF_PRESET_MAX_SPEED: top,
            }
    return result


def _migration_issue_id(entry: TionConfigEntry) -> str:
    return f"account_not_migrated_{entry.entry_id}"


def _update_migration_issue(
    hass: HomeAssistant, entry: TionConfigEntry, account: TionAccount
) -> None:
    """Ask to move the account in the Tion app while a location is on the old cloud."""
    if any(location.needs_migration for location in account.locations):
        ir.async_create_issue(
            hass,
            DOMAIN,
            _migration_issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="account_not_migrated",
            translation_placeholders={"email": entry.title},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, _migration_issue_id(entry))


def _register_devices(
    hass: HomeAssistant, entry: TionConfigEntry, account: TionAccount
) -> None:
    registry = dr.async_get(hass)
    for device in account.devices():
        if view(device) is None:
            _LOGGER.debug("Skipping unsupported Tion device model %s", device.model)
            continue
        room = account.room_of(device)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.id)},
            connections={(dr.CONNECTION_NETWORK_MAC, mac) for mac in device.macs},
            manufacturer=MANUFACTURER,
            model=MODEL_NAMES[device.product_id],
            model_id=device.product_id,
            name=device.name,
            sw_version=str(device.firmware),
            hw_version=str(device.hardware),
            suggested_area=room.name if room is not None else None,
        )
