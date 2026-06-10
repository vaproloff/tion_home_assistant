"""Per-breezer speed preset controller for Tion breezers."""

from __future__ import annotations

from homeassistant.components.climate import PRESET_NONE

from .const import CONF_PRESET_MAX_SPEED, CONF_PRESET_MIN_SPEED

ATTR_SAVED_MIN_SPEED = "preset_saved_min_speed"
ATTR_SAVED_MAX_SPEED = "preset_saved_max_speed"
# How many coordinator updates may report stale limits before we assume our
# write was applied and stop suppressing reconcile resets. The coordinator's
# stale-command tracking already drops most stale refreshes, so a small bound
# is enough to cover eventual consistency while preventing a permanently stuck
# gate when a cloud write is silently dropped. At the default 60s scan
# interval this is roughly a 3-minute ceiling before the gate gives up.
PENDING_CONFIRM_POLLS = 3


class TionPresetController:
    """Manage speed presets for a single breezer.

    Pure logic with no Home Assistant dependencies so it can be unit-tested in
    isolation. The owning climate entity performs all I/O.
    """

    def __init__(self, presets: dict[str, dict[str, int]]) -> None:
        """Initialize the controller from stored preset options."""
        self._presets = presets
        self._active = PRESET_NONE
        self._saved_min: int | None = None
        self._saved_max: int | None = None
        # Limits we pushed to the cloud but have not seen confirmed yet. While
        # set, reconcile() must not treat a divergence as an external change,
        # because the cloud is eventually-consistent.
        self._pending: tuple[int, int] | None = None
        self._pending_polls = 0

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

    def _expected_limits(self) -> tuple[int, int] | None:
        """Return (min, max) of the active preset, or None for PRESET_NONE."""
        if self._active == PRESET_NONE:
            return None
        preset = self._presets[self._active]
        return int(preset[CONF_PRESET_MIN_SPEED]), int(preset[CONF_PRESET_MAX_SPEED])

    def activate(
        self, preset: str, cur_min: object, cur_max: object
    ) -> tuple[int, int]:
        """Switch to a preset, return the (min, max) limits to push to the cloud."""
        if preset == PRESET_NONE:
            limits = (
                self._saved_min if self._saved_min is not None else int(cur_min),
                self._saved_max if self._saved_max is not None else int(cur_max),
            )
            self._active = PRESET_NONE
            self.reset_saved()
            self._pending = limits
            self._pending_polls = 0
            return limits

        if self._active == PRESET_NONE:
            self._saved_min = int(cur_min)
            self._saved_max = int(cur_max)

        self._active = preset
        limits = self._expected_limits()
        self._pending = limits
        self._pending_polls = 0
        return limits

    def reconcile(self, reported_min: object, reported_max: object) -> bool:
        """Detect external limit changes, resetting to PRESET_NONE if needed.

        Returns True when the preset state changed.
        """
        try:
            reported = (int(reported_min), int(reported_max))
        except (TypeError, ValueError):
            return False

        if self._pending is not None:
            if reported == self._pending:
                self._pending = None
                self._pending_polls = 0
            else:
                self._pending_polls += 1
                if self._pending_polls >= PENDING_CONFIRM_POLLS:
                    self._pending = None
                    self._pending_polls = 0
            return False

        if self._active == PRESET_NONE:
            return False

        if reported != self._expected_limits():
            self._active = PRESET_NONE
            self.reset_saved()
            return True

        return False

    def reset_saved(self) -> None:
        """Clear the saved pre-preset limits."""
        self._saved_min = None
        self._saved_max = None

    def restore(
        self, active: str, saved_min: int | None, saved_max: int | None
    ) -> None:
        """Rehydrate state after a Home Assistant restart."""
        if active not in self.preset_modes:
            return
        self._active = active
        self._saved_min = saved_min
        self._saved_max = saved_max
        self._pending = None
        self._pending_polls = 0

    def restore_attributes(self) -> dict[str, int | None]:
        """Return saved limits for the entity's extra_state_attributes."""
        return {
            ATTR_SAVED_MIN_SPEED: self._saved_min,
            ATTR_SAVED_MAX_SPEED: self._saved_max,
        }
