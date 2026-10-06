"""Speed presets of Tion breezers.

A preset is a named fan regime of one breezer: a fixed speed, or local PID within
its own speed limits. Applying a preset remembers the regime it replaced (the
baseline): a speed with the power state, or local PID. Leaving the preset returns
to the baseline. Pure logic: the climate entity sends the commands.
"""

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


@dataclass(frozen=True, slots=True)
class ManualPreset:
    """Run the breezer at a fixed speed; applying it turns the breezer on."""

    speed: int


@dataclass(frozen=True, slots=True)
class PidPreset:
    """Run local PID within these speed limits instead of its usual ones."""

    min_speed: int
    max_speed: int


type Preset = ManualPreset | PidPreset


def preset_from_config(cfg: Mapping[str, Any]) -> Preset | None:
    """Build a preset from its options; None for a type this version lacks."""
    match cfg.get(CONF_PRESET_TYPE):
        case TionPresetType.MANUAL:
            return ManualPreset(int(cfg[CONF_PRESET_SPEED]))
        case TionPresetType.LOCAL_PID:
            return PidPreset(
                int(cfg[CONF_PRESET_MIN_SPEED]), int(cfg[CONF_PRESET_MAX_SPEED])
            )
    return None


def visible_presets(
    configs: Mapping[str, Mapping[str, Any]], *, pid_configured: bool
) -> dict[str, Preset]:
    """Return the presets a breezer offers: PID ones only while PID is set up."""
    presets: dict[str, Preset] = {}
    for name, cfg in configs.items():
        preset = preset_from_config(cfg)
        if isinstance(preset, ManualPreset) or (
            isinstance(preset, PidPreset) and pid_configured
        ):
            presets[name] = preset
    return presets


@dataclass(frozen=True, slots=True)
class SpeedBaseline:
    """The breezer ran at this speed (None if unknown), on or off."""

    speed: int | None
    is_on: bool


@dataclass(frozen=True, slots=True)
class PidBaseline:
    """The breezer ran under local PID."""


type Baseline = SpeedBaseline | PidBaseline


def baseline_to_storage(baseline: Baseline) -> dict[str, Any]:
    """Serialize a baseline for the climate entity's restore data."""
    if isinstance(baseline, PidBaseline):
        return {"type": TionPresetType.LOCAL_PID.value}
    return {
        "type": TionPresetType.MANUAL.value,
        "speed": baseline.speed,
        "is_on": baseline.is_on,
    }


def baseline_from_storage(data: Mapping[str, Any] | None) -> Baseline | None:
    """Rebuild a stored baseline; None for anything else, e.g. an older format."""
    if not data:
        return None
    match data.get("type"):
        case TionPresetType.LOCAL_PID:
            return PidBaseline()
        case TionPresetType.MANUAL:
            speed = data.get("speed")
            is_on = data.get("is_on")
            if isinstance(is_on, bool) and (
                speed is None
                or (isinstance(speed, int) and not isinstance(speed, bool))
            ):
                return SpeedBaseline(speed, is_on)
    return None


class TionPresetController:
    """Track the active preset of one breezer and the baseline it replaced."""

    def __init__(self, presets: Mapping[str, Preset]) -> None:
        """Start without an active preset."""
        self._presets = dict(presets)
        self._active = PRESET_NONE
        self._saved: Baseline | None = None

    @property
    def has_presets(self) -> bool:
        """Return whether the breezer offers any preset."""
        return bool(self._presets)

    @property
    def preset_modes(self) -> list[str]:
        """Return PRESET_NONE and the preset names."""
        return [PRESET_NONE, *self._presets]

    @property
    def preset_mode(self) -> str:
        """Return the active preset name, or PRESET_NONE."""
        return self._active

    @property
    def saved(self) -> Baseline | None:
        """Return the baseline the active preset replaced."""
        return self._saved

    def preset(self, name: str) -> Preset | None:
        """Return a preset by name; None for PRESET_NONE or an unknown name."""
        return self._presets.get(name)

    def active_preset(self) -> Preset | None:
        """Return the active preset, or None."""
        return self._presets.get(self._active)

    def activate(self, name: str, baseline: Baseline) -> None:
        """Make a preset active; the baseline is kept from the first activation."""
        if self._active == PRESET_NONE:
            self._saved = baseline
        self._active = name

    def deactivate(self) -> None:
        """Drop back to PRESET_NONE and forget the baseline."""
        self._active = PRESET_NONE
        self._saved = None

    def restore(self, active: str, saved: Baseline | None) -> None:
        """Rehydrate after a restart; an unknown preset name is ignored."""
        if active not in self.preset_modes:
            return
        self._active = active
        self._saved = saved
