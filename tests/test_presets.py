"""Tests for the Tion breezer speed preset controller."""

from homeassistant.components.climate import PRESET_NONE

from custom_components.tion.presets import TionPresetController

PRESETS = {
    "eco": {"min_speed": 1, "max_speed": 2},
    "boost": {"min_speed": 4, "max_speed": 6},
}


def _controller() -> TionPresetController:
    """Return a controller with two presets."""
    return TionPresetController(dict(PRESETS))


def test_no_presets() -> None:
    """Test a controller without presets reports none configured."""
    controller = TionPresetController({})

    assert controller.has_presets is False
    assert controller.preset_modes == [PRESET_NONE]
    assert controller.preset_mode == PRESET_NONE


def test_preset_modes_lists_none_and_configured() -> None:
    """Test preset_modes starts with PRESET_NONE then configured names."""
    controller = _controller()

    assert controller.has_presets is True
    assert controller.preset_modes == [PRESET_NONE, "eco", "boost"]


def test_activate_from_none_saves_current_limits() -> None:
    """Test activating from PRESET_NONE saves current limits and returns preset limits."""
    controller = _controller()

    limits = controller.activate("boost", 1, 3)

    assert limits == (4, 6)
    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        "preset_saved_min_speed": 1,
        "preset_saved_max_speed": 3,
    }


def test_activate_preset_to_preset_keeps_saved() -> None:
    """Test switching preset to preset does not overwrite saved_none."""
    controller = _controller()
    controller.activate("boost", 1, 3)

    limits = controller.activate("eco", 4, 6)

    assert limits == (1, 2)
    assert controller.preset_mode == "eco"
    assert controller.restore_attributes() == {
        "preset_saved_min_speed": 1,
        "preset_saved_max_speed": 3,
    }


def test_activate_to_none_restores_and_resets() -> None:
    """Test returning to PRESET_NONE restores saved limits and clears them."""
    controller = _controller()
    controller.activate("boost", 1, 3)

    limits = controller.activate(PRESET_NONE, 4, 6)

    assert limits == (1, 3)
    assert controller.preset_mode == PRESET_NONE
    assert controller.restore_attributes() == {
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }


def test_activate_none_without_active_preset_returns_current() -> None:
    """Test activating PRESET_NONE with no saved state returns current limits."""
    controller = _controller()

    limits = controller.activate(PRESET_NONE, 2, 4)

    assert limits == (2, 4)
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_on_external_change() -> None:
    """Test a divergent reported limit resets the active preset to PRESET_NONE.

    The coordinator only surfaces reads taken after our command completed, so a
    divergence is a genuine external change and resets immediately.
    """
    controller = _controller()
    controller.activate("boost", 1, 3)

    assert controller.reconcile(2, 5) is True
    assert controller.preset_mode == PRESET_NONE
    assert controller.restore_attributes() == {
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }


def test_reconcile_no_reset_when_matches() -> None:
    """Test no reset while reported limits keep matching the active preset."""
    controller = _controller()
    controller.activate("boost", 1, 3)

    assert controller.reconcile(4, 6) is False
    assert controller.preset_mode == "boost"


def test_reconcile_coerces_string_values() -> None:
    """Test reconcile coerces API string values to int before comparing."""
    controller = _controller()
    controller.activate("boost", 1, 3)

    # String "4"/"6" must coerce and match the active preset's limits.
    assert controller.reconcile("4", "6") is False
    assert controller.preset_mode == "boost"

    # A real divergence is detected and resets.
    assert controller.reconcile(2, 5) is True
    assert controller.preset_mode == PRESET_NONE


def test_restore_rehydrates_active_preset_and_saved() -> None:
    """Test restore sets the active preset and saved limits after a restart."""
    controller = _controller()

    controller.restore("boost", 1, 3)

    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        "preset_saved_min_speed": 1,
        "preset_saved_max_speed": 3,
    }
    # A later divergence is treated as an external change.
    assert controller.reconcile(2, 5) is True
    assert controller.preset_mode == PRESET_NONE


def test_restore_ignores_unknown_preset() -> None:
    """Test restore ignores a preset name that is not configured."""
    controller = _controller()

    controller.restore("nonexistent", 1, 3)

    assert controller.preset_mode == PRESET_NONE
