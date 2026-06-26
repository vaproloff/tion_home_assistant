"""Per-breezer speed preset controller for Tion breezers.

A preset is a named set of desired breezer fields. Applying a preset writes
those fields into the reconciler; returning to ``PRESET_NONE`` restores the
baseline (the desired overlay and mode that were in effect before the preset).
The baseline is captured from the desired overlay, never from a live snapshot,
so a cancelled-then-retried apply cannot pollute it.
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

ATTR_SAVED_PRESET = "preset_saved"


@dataclass(frozen=True)
class Preset(ABC):
    """A speed intent that knows its desired fields, mode, and serialization."""

    @abstractmethod
    def desired_fields(self) -> dict[str, Any]:
        """Return the breezer desired fields this preset overlays."""

    @abstractmethod
    def is_auto(self) -> bool:
        """Return whether this preset runs the breezer in auto mode."""

    @classmethod
    def from_config(cls, cfg: Mapping[str, int | str]) -> Preset:
        """Build a preset from an options-flow preset dict."""
        if cfg[CONF_PRESET_TYPE] == TionPresetType.MANUAL:
            return ManualPreset(int(cfg[CONF_PRESET_SPEED]))
        return AutoPreset(
            int(cfg[CONF_PRESET_MIN_SPEED]), int(cfg[CONF_PRESET_MAX_SPEED])
        )


@dataclass(frozen=True)
class ManualPreset(Preset):
    """A preset that pins the breezer to a fixed manual speed."""

    speed: int

    def desired_fields(self) -> dict[str, Any]:
        """Pin the breezer on at the fixed speed."""
        return {"is_on": True, "speed": self.speed}

    def is_auto(self) -> bool:
        """A manual preset does not run in auto."""
        return False


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


@dataclass(frozen=True)
class PresetBaseline:
    """The desired overlay and mode in effect before a preset was activated."""

    overrides: dict[str, Any]
    was_auto: bool

    def to_storage(self) -> dict[str, Any]:
        """Serialize the baseline for persistence."""
        return {"overrides": dict(self.overrides), "was_auto": self.was_auto}

    @classmethod
    def from_storage(cls, data: Mapping[str, Any] | None) -> PresetBaseline | None:
        """Rebuild a baseline from restored state attributes."""
        if not data:
            return None
        return cls(overrides=dict(data["overrides"]), was_auto=bool(data["was_auto"]))


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
        self._saved: PresetBaseline | None = None

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
    def saved(self) -> PresetBaseline | None:
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

    def activate(self, name: str, baseline: PresetBaseline) -> None:
        """Switch to a preset, capturing the baseline on the first activation.

        The baseline is supplied by the caller from the current desired overlay
        (not a live snapshot), and is preserved across preset-to-preset switches.
        """
        if self._active == PRESET_NONE:
            self._saved = baseline
        self._active = name

    def deactivate(self) -> None:
        """Drop back to PRESET_NONE and clear the saved baseline."""
        self._active = PRESET_NONE
        self._saved = None

    def restore(self, active: str, saved: PresetBaseline | None) -> None:
        """Rehydrate state after a Home Assistant restart."""
        if active not in self.preset_modes:
            return
        self._active = active
        self._saved = saved

    def restore_attributes(self) -> dict[str, dict[str, Any] | None]:
        """Return the saved baseline for the entity's extra_state_attributes."""
        return {ATTR_SAVED_PRESET: self._saved.to_storage() if self._saved else None}
