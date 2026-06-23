"""Per-breezer speed preset controller for Tion breezers."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from homeassistant.components.climate import FAN_AUTO, PRESET_NONE

from .const import (
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    TionPresetType,
)

ATTR_SAVED_PRESET = "preset_saved"


class PresetTarget(Protocol):
    """The narrow entity surface a preset needs to apply and snapshot itself."""

    @property
    def fan_mode(self) -> str | None:
        """The breezer's current fan mode, or None if unknown."""

    @property
    def speed_min_set(self) -> int | None:
        """The configured minimum auto speed."""

    @property
    def speed_max_set(self) -> int | None:
        """The configured maximum auto speed."""

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the breezer's fan mode."""

    async def async_apply_auto_limits(self, min_speed: int, max_speed: int) -> None:
        """Switch to auto mode and apply the given speed limits."""


@dataclass(frozen=True)
class Preset(ABC):
    """A speed intent that knows how to apply, serialize, and compare itself."""

    @abstractmethod
    async def apply(self, target: PresetTarget) -> None:
        """Apply this preset's intent to the breezer."""

    @abstractmethod
    def to_storage(self) -> dict[str, int | str]:
        """Serialize the preset for persistence in state attributes."""

    @classmethod
    def from_config(cls, cfg: Mapping[str, int | str]) -> Preset:
        """Build a preset from an options-flow preset dict."""
        if cfg[CONF_PRESET_TYPE] == TionPresetType.MANUAL:
            return ManualPreset(int(cfg[CONF_PRESET_SPEED]))
        return AutoPreset(
            int(cfg[CONF_PRESET_MIN_SPEED]), int(cfg[CONF_PRESET_MAX_SPEED])
        )

    @classmethod
    def from_storage(cls, data: Mapping[str, int | str] | None) -> Preset | None:
        """Rebuild a saved preset from restored state attributes."""
        if not data:
            return None
        return cls.from_config(data)

    @classmethod
    def snapshot(cls, target: PresetTarget) -> Preset | None:
        """Capture the breezer's current speed intent, or None if unreadable.

        A single try/except so any unreadable field (None or non-numeric
        min/max/speed) yields None instead of raising.
        """
        fan_mode = target.fan_mode
        if fan_mode is None:
            return None
        try:
            if fan_mode == FAN_AUTO:
                return AutoPreset(int(target.speed_min_set), int(target.speed_max_set))
            return ManualPreset(int(fan_mode))
        except TypeError, ValueError:
            return None


@dataclass(frozen=True)
class ManualPreset(Preset):
    """A preset that pins the breezer to a fixed manual speed."""

    speed: int

    async def apply(self, target: PresetTarget) -> None:
        """Apply the manual speed via the breezer's fan mode."""
        await target.async_set_fan_mode(str(self.speed))

    def to_storage(self) -> dict[str, int | str]:
        """Serialize the manual preset."""
        return {
            CONF_PRESET_TYPE: TionPresetType.MANUAL.value,
            CONF_PRESET_SPEED: self.speed,
        }


@dataclass(frozen=True)
class AutoPreset(Preset):
    """A preset that runs the breezer in auto with speed limits."""

    min_speed: int
    max_speed: int

    async def apply(self, target: PresetTarget) -> None:
        """Apply the auto-mode speed limits to the breezer."""
        await target.async_apply_auto_limits(self.min_speed, self.max_speed)

    def to_storage(self) -> dict[str, int | str]:
        """Serialize the auto preset."""
        return {
            CONF_PRESET_TYPE: TionPresetType.AUTO.value,
            CONF_PRESET_MIN_SPEED: self.min_speed,
            CONF_PRESET_MAX_SPEED: self.max_speed,
        }


@dataclass(frozen=True)
class PresetControllerState:
    """A restorable snapshot of preset controller state."""

    active: str
    saved: Preset | None


class TionPresetController:
    """Manage speed presets for a single breezer.

    Pure logic with no Home Assistant dependencies so it can be unit-tested in
    isolation. The owning climate entity performs all I/O.

    Each preset is a :class:`Preset`; reconciliation is value equality between the
    breezer's current snapshot and the active preset.
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

    def preset(self, name: str) -> Preset | None:
        """Return a configured preset by name, or None for PRESET_NONE/unknown."""
        return self._presets.get(name)

    def checkpoint(self) -> PresetControllerState:
        """Return the current preset state for rollback."""
        return PresetControllerState(self._active, self._saved)

    def restore_checkpoint(self, state: PresetControllerState) -> None:
        """Restore a preset state captured before an optimistic transition."""
        self._active = state.active
        self._saved = state.saved

    def activate(self, name: str, current: Preset | None) -> Preset | None:
        """Switch to a preset (or PRESET_NONE), returning the preset to apply."""
        if name == PRESET_NONE:
            target = self._saved if self._saved is not None else current
            self._active = PRESET_NONE
            self._saved = None
            return target

        if self._active == PRESET_NONE:
            self._saved = current

        self._active = name
        return self._presets[name]

    def reconcile(self, current: Preset | None) -> bool:
        """Reset to PRESET_NONE when the breezer diverged from the active preset.

        Returns True when the preset state changed. An unreadable snapshot
        (``current is None``) is never treated as divergence.
        """
        if self._active == PRESET_NONE or current is None:
            return False
        if current != self._presets[self._active]:
            self._active = PRESET_NONE
            self._saved = None
            return True
        return False

    def restore(self, active: str, saved: Preset | None) -> None:
        """Rehydrate state after a Home Assistant restart."""
        if active not in self.preset_modes:
            return
        self._active = active
        self._saved = saved

    def restore_attributes(self) -> dict[str, dict[str, int | str] | None]:
        """Return the saved preset for the entity's extra_state_attributes."""
        return {ATTR_SAVED_PRESET: self._saved.to_storage() if self._saved else None}
