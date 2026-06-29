"""Sparse desired-state value objects for Tion breezers and zones.

Each object holds only the fields a writer (manual command, preset, PID) has
explicitly set. Unset fields are taken from the reported cloud state when a
full API payload is built, so ``None`` as a value is distinct from "absent".
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .client import TionZone, TionZoneDevice


def _int_or_default(value: Any, default: int | None) -> int | None:
    """Convert an API value to int or return a default."""
    try:
        return int(value)
    except TypeError, ValueError:
        return default


# send_breezer payload fields carried over from reported state when not desired.
_BREEZER_FIELDS = (
    "is_on",
    "speed",
    "speed_min_set",
    "speed_max_set",
    "heater_enabled",
    "heater_mode",
    "gate",
)


@dataclass(frozen=True)
class DesiredBreezer:
    """Desired breezer fields; only explicitly-set keys are present."""

    fields: Mapping[str, Any]

    def merge(self, reported: TionZoneDevice) -> dict[str, Any] | None:
        """Build the full send_breezer payload: reported overlaid with desired."""
        t_set = _int_or_default(self.fields.get("t_set", reported.data.t_set), None)
        if t_set is None:
            return None
        payload: dict[str, Any] = {"guid": reported.guid, "t_set": t_set}
        for key in _BREEZER_FIELDS:
            payload[key] = self.fields.get(key, getattr(reported.data, key))

        speed = _int_or_default(payload["speed"], None)
        if speed is not None:
            payload["speed"] = max(1, speed)
        return payload


@dataclass(frozen=True)
class DesiredZone:
    """Desired zone fields (mode, co2)."""

    fields: Mapping[str, Any]

    def merge(self, reported: TionZone) -> dict[str, Any] | None:
        """Build the full send_zone payload: reported overlaid with desired."""
        co2 = _int_or_default(self.fields.get("co2", reported.mode.auto_set.co2), None)
        if co2 is None:
            return None
        mode = self.fields.get("mode", reported.mode.current)
        return {"guid": reported.guid, "mode": mode, "co2": co2}
