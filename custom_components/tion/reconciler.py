"""Single desired-state reconciler for the Tion coordinator.

Drives cloud state toward the desired state idempotently: every cycle it
compares the per-guid desired fields against the reported snapshot and, for
divergent unconfirmed fields, dispatches a background command. A field that
diverges *after* it was confirmed is treated as an external change and dropped
(per-field), so the reconciler never fights a change made outside Home
Assistant. Sends are fire-and-forget; the next cycle re-sends if still needed.
"""

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .client import TionError, TionZone, TionZoneDevice
from .desired_state import DesiredBreezer, DesiredZone

if TYPE_CHECKING:
    from .coordinator import TionData, TionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

_BREEZER_PAYLOAD_FIELDS = (
    "is_on",
    "speed",
    "t_set",
    "speed_min_set",
    "speed_max_set",
    "heater_enabled",
    "heater_mode",
    "gate",
)


class TionReconciler:
    """Converge cloud state to the desired state, in the background."""

    def __init__(self, coordinator: TionDataUpdateCoordinator) -> None:
        """Initialize the reconciler with empty desired state."""
        self.coordinator = coordinator
        self._breezers: dict[str, dict[str, Any]] = {}
        self._zones: dict[str, dict[str, Any]] = {}
        self._confirmed: dict[str, set[str]] = {}
        self._inflight: set[str] = set()

    def set_breezer(self, guid: str, fields: Mapping[str, Any]) -> None:
        """Merge fields into a breezer's desired state (an explicit intent)."""
        self._breezers.setdefault(guid, {}).update(fields)
        self._confirmed.setdefault(guid, set()).difference_update(fields)

    def set_zone(self, guid: str, fields: Mapping[str, Any]) -> None:
        """Merge fields into a zone's desired state (an explicit intent)."""
        self._zones.setdefault(guid, {}).update(fields)
        self._confirmed.setdefault(guid, set()).difference_update(fields)

    def reconcile(self, data: TionData) -> None:
        """Send commands to converge reported state toward desired state."""
        handled_zones: set[str] = set()
        for guid in list(self._breezers):
            zone = data.zone(guid)
            if zone is not None and zone.guid is not None:
                handled_zones.add(zone.guid)
            self._reconcile_breezer(guid, data)
        for zone_guid in list(self._zones):
            if zone_guid not in handled_zones:
                self._reconcile_zone_only(zone_guid, data)

    def _reconcile_breezer(self, guid: str, data: TionData) -> None:
        device = data.device(guid)
        if device is None or guid in self._inflight:
            return
        breezer_payload = self._resolve_breezer(guid, device)
        zone = data.zone(guid)
        zone_payload = None
        if (
            zone is not None
            and zone.guid in self._zones
            and zone.guid not in self._inflight
        ):
            zone_payload = self._resolve_zone(zone.guid, zone)
        if breezer_payload is None and zone_payload is None:
            return
        if breezer_payload is not None:
            self._apply_breezer(device, breezer_payload)
        keys = [guid]
        if zone_payload is not None:
            keys.append(zone_payload["guid"])
        self._dispatch(guid, keys, zone_payload, breezer_payload)

    def _reconcile_zone_only(self, zone_guid: str, data: TionData) -> None:
        if zone_guid in self._inflight:
            return
        zone = self._find_zone(data, zone_guid)
        if zone is None:
            return
        zone_payload = self._resolve_zone(zone_guid, zone)
        if zone_payload is None:
            return
        self._dispatch(f"zone_{zone_guid}", [zone_guid], zone_payload, None)

    def _resolve_breezer(
        self, guid: str, device: TionZoneDevice
    ) -> dict[str, Any] | None:
        fields = self._breezers[guid]
        baseline = DesiredBreezer({}).merge(device)
        if baseline is None:
            return None
        if not self._detect(guid, fields, baseline):
            return None
        return DesiredBreezer(fields).merge(device)

    def _resolve_zone(self, zone_guid: str, zone: TionZone) -> dict[str, Any] | None:
        fields = self._zones[zone_guid]
        baseline = DesiredZone({}).merge(zone)
        if baseline is None:
            return None
        if not self._detect(zone_guid, fields, baseline):
            return None
        return DesiredZone(fields).merge(zone)

    def _detect(
        self, key: str, fields: dict[str, Any], baseline: Mapping[str, Any]
    ) -> bool:
        """Per-field detect; mutate fields/confirmed, return whether to send.

        Confirms reached fields, drops fields that diverged after confirmation
        (external change), and flags unconfirmed divergent fields for sending.
        """
        confirmed = self._confirmed.setdefault(key, set())
        need_send = False
        for field in list(fields):
            if fields[field] == baseline.get(field):
                confirmed.add(field)
            elif field in confirmed:
                del fields[field]
                confirmed.discard(field)
            else:
                need_send = True
        return need_send and bool(fields)

    @staticmethod
    def _apply_breezer(device: TionZoneDevice, payload: Mapping[str, Any]) -> None:
        for field in _BREEZER_PAYLOAD_FIELDS:
            setattr(device.data, field, payload[field])

    @staticmethod
    def _find_zone(data: TionData, zone_guid: str) -> TionZone | None:
        for location in data.locations:
            for zone in location.zones:
                if zone.guid == zone_guid:
                    return zone
        return None

    def _dispatch(
        self,
        name: str,
        keys: list[str],
        zone_payload: Mapping[str, Any] | None,
        breezer_payload: Mapping[str, Any] | None,
    ) -> None:
        self._inflight.update(keys)
        self.coordinator.config_entry.async_create_background_task(
            self.coordinator.hass,
            self._send(zone_payload, breezer_payload, keys),
            f"tion_reconcile_{name}",
        )

    async def _send(
        self,
        zone_payload: Mapping[str, Any] | None,
        breezer_payload: Mapping[str, Any] | None,
        keys: list[str],
    ) -> None:
        try:
            if zone_payload is not None:
                await self.coordinator.async_send_zone(
                    guid=zone_payload["guid"],
                    mode=zone_payload["mode"],
                    co2=zone_payload["co2"],
                    request_refresh=False,
                    track_stale=False,
                )
            if breezer_payload is not None:
                await self.coordinator.async_send_breezer(
                    **breezer_payload, request_refresh=False, track_stale=False
                )
        except TionError as err:
            _LOGGER.warning("Tion reconcile send failed for %s: %s", keys, err)
        finally:
            self._inflight.difference_update(keys)
