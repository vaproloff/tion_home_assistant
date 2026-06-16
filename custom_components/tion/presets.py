"""Per-breezer speed preset controller for Tion breezers."""

from homeassistant.components.climate import PRESET_NONE

from .const import (
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    TionPresetType,
)

ATTR_SAVED_SPEED = "preset_saved_speed"
ATTR_SAVED_MIN_SPEED = "preset_saved_min_speed"
ATTR_SAVED_MAX_SPEED = "preset_saved_max_speed"

# (fan_speed, min_speed, max_speed): fan_speed set => manual, limits set => auto.
FanIntent = tuple[int | None, int | None, int | None]


class TionPresetController:
    """Manage speed presets for a single breezer.

    Pure logic with no Home Assistant dependencies so it can be unit-tested in
    isolation. The owning climate entity performs all I/O.

    A preset is a "fan intent": manual at a target speed ``(speed, None, None)``
    or auto with limits ``(None, min, max)``. Comparing the current intent with a
    preset's expected intent detects external changes.
    """

    def __init__(self, presets: dict[str, dict[str, int | str]]) -> None:
        """Initialize the controller from stored preset options."""
        self._presets = presets
        self._active = PRESET_NONE
        self._saved: FanIntent | None = None

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

    def expected_intent(self, name: str) -> FanIntent | None:
        """Return the fan intent a preset represents, or None for PRESET_NONE."""
        preset = self._presets.get(name)
        if preset is None:
            return None
        if preset[CONF_PRESET_TYPE] == TionPresetType.MANUAL:
            return (int(preset[CONF_PRESET_SPEED]), None, None)
        return (
            None,
            int(preset[CONF_PRESET_MIN_SPEED]),
            int(preset[CONF_PRESET_MAX_SPEED]),
        )

    def activate(self, name: str, current: FanIntent | None) -> FanIntent | None:
        """Switch to a preset (or PRESET_NONE), returning the intent to apply."""
        if name == PRESET_NONE:
            target = self._saved if self._saved is not None else current
            self._active = PRESET_NONE
            self._saved = None
            return target

        if self._active == PRESET_NONE:
            self._saved = current

        self._active = name
        return self.expected_intent(name)

    def reconcile(self, current: FanIntent) -> bool:
        """Reset to PRESET_NONE when the breezer diverged from the active preset.

        Returns True when the preset state changed.
        """
        if self._active == PRESET_NONE:
            return False
        if current != self.expected_intent(self._active):
            self._active = PRESET_NONE
            self._saved = None
            return True
        return False

    def restore(
        self,
        active: str,
        saved_speed: int | None,
        saved_min: int | None,
        saved_max: int | None,
    ) -> None:
        """Rehydrate state after a Home Assistant restart."""
        if active not in self.preset_modes:
            return
        self._active = active
        if saved_speed is None and saved_min is None and saved_max is None:
            self._saved = None
        else:
            self._saved = (saved_speed, saved_min, saved_max)

    def restore_attributes(self) -> dict[str, int | None]:
        """Return saved intent fields for the entity's extra_state_attributes."""
        speed, min_speed, max_speed = self._saved or (None, None, None)
        return {
            ATTR_SAVED_SPEED: speed,
            ATTR_SAVED_MIN_SPEED: min_speed,
            ATTR_SAVED_MAX_SPEED: max_speed,
        }
