"""Per-breezer speed preset controller for Tion breezers.

A preset is a named set of desired breezer fields. Applying a preset writes
those fields into the reconciler; returning to ``PRESET_NONE`` restores the
baseline -- the breezer regime that was in effect before the preset. The
baseline is itself an anonymous ``Preset`` (a manual speed or auto limits, with
its power state), so restoring it re-uses the same desired-write path and the
regime is carried by the object's type rather than a separate flag.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.climate import PRESET_NONE

from .const import (
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    TionPresetType,
)


@dataclass(frozen=True)
class Preset(ABC):
    """A speed intent that knows its desired fields, mode, and serialization."""

    @abstractmethod
    def desired_fields(self) -> dict[str, Any]:
        """Return the breezer desired fields this preset overlays."""

    @abstractmethod
    def is_auto(self) -> bool:
        """Return whether this preset runs the breezer in auto mode."""

    @abstractmethod
    def to_storage(self) -> dict[str, Any]:
        """Serialize the preset for persistence."""

    @classmethod
    def from_config(cls, cfg: Mapping[str, int | str]) -> Preset:
        """Build a preset from an options-flow preset dict."""
        if cfg[CONF_PRESET_TYPE] == TionPresetType.MANUAL:
            return ManualPreset(int(cfg[CONF_PRESET_SPEED]))
        return AutoPreset(
            int(cfg[CONF_PRESET_MIN_SPEED]), int(cfg[CONF_PRESET_MAX_SPEED])
        )

    @classmethod
    def from_storage(cls, data: Mapping[str, Any] | None) -> Preset | None:
        """Rebuild a preset from a restored storage payload.

        Returns None for an unrecognized payload -- e.g. a baseline persisted by
        an older version in a different shape -- so a stale restore is dropped
        instead of crashing the entity; the baseline is rebuilt on the next
        preset change.
        """
        if not data:
            return None
        preset_type = data.get("type")
        if preset_type == TionPresetType.MANUAL.value:
            return ManualPreset(int(data["speed"]), bool(data["is_on"]))
        if preset_type == TionPresetType.AUTO.value:
            return AutoPreset(int(data["min_speed"]), int(data["max_speed"]))
        return None


@dataclass(frozen=True)
class ManualPreset(Preset):
    """A preset that pins the breezer to a fixed manual speed."""

    speed: int
    is_on: bool = True

    def desired_fields(self) -> dict[str, Any]:
        """Pin the breezer at the fixed speed and power state."""
        return {"is_on": self.is_on, "speed": self.speed}

    def is_auto(self) -> bool:
        """A manual preset does not run in auto."""
        return False

    def to_storage(self) -> dict[str, Any]:
        """Serialize the manual preset."""
        return {
            "type": TionPresetType.MANUAL.value,
            "speed": self.speed,
            "is_on": self.is_on,
        }


@dataclass(frozen=True)
class AutoPreset(Preset):
    """A preset that runs the breezer in auto with speed limits."""

    min_speed: int
    max_speed: int

    def desired_fields(self) -> dict[str, Any]:
        """Overlay the auto-mode speed limits."""
        return {"speed_min_set": self.min_speed, "speed_max_set": self.max_speed}

    def is_auto(self) -> bool:
        """An auto preset runs in auto."""
        return True

    def to_storage(self) -> dict[str, Any]:
        """Serialize the auto preset."""
        return {
            "type": TionPresetType.AUTO.value,
            "min_speed": self.min_speed,
            "max_speed": self.max_speed,
        }


class TionPresetController:
    """Manage speed presets for a single breezer.

    Pure logic with no Home Assistant dependencies so it can be unit-tested in
    isolation. The owning climate entity performs all I/O and reconciler writes.
    """

    def __init__(self, presets: dict[str, dict[str, int | str]]) -> None:
        """Initialize the controller from stored preset options."""
        self._presets: dict[str, Preset] = {
            name: Preset.from_config(cfg) for name, cfg in presets.items()
        }
        self._active = PRESET_NONE
        self._saved: Preset | None = None

    @property
    def has_presets(self) -> bool:
        """Return whether any preset is configured."""
        return bool(self._presets)

    @property
    def preset_modes(self) -> list[str]:
        """Return available preset modes."""
        return [PRESET_NONE, *self._presets]

    @property
    def preset_mode(self) -> str:
        """Return the active preset mode."""
        return self._active

    @property
    def saved(self) -> Preset | None:
        """Return the baseline saved before the active preset, if any."""
        return self._saved

    @property
    def managed_fields(self) -> set[str]:
        """Return the union of breezer fields any configured preset overlays."""
        fields: set[str] = set()
        for preset in self._presets.values():
            fields.update(preset.desired_fields())
        return fields

    def preset(self, name: str) -> Preset | None:
        """Return a configured preset by name, or None for PRESET_NONE/unknown."""
        return self._presets.get(name)

    def active_preset(self) -> Preset | None:
        """Return the active Preset object, or None when PRESET_NONE."""
        return self._presets.get(self._active)

    def activate(self, name: str, baseline: Preset) -> None:
        """Switch to a preset, capturing the baseline on the first activation.

        The baseline is the anonymous preset the caller built from the current
        regime; it is captured only on the first activation, so it is preserved
        across preset-to-preset switches.
        """
        if self._active == PRESET_NONE:
            self._saved = baseline
        self._active = name

    def deactivate(self) -> None:
        """Drop back to PRESET_NONE and clear the saved baseline."""
        self._active = PRESET_NONE
        self._saved = None

    def restore(self, active: str, saved: Preset | None) -> None:
        """Rehydrate state after a Home Assistant restart."""
        if active not in self.preset_modes:
            return
        self._active = active
        self._saved = saved
