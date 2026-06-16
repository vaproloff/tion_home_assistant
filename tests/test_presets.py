"""Tests for the Tion breezer speed preset controller."""

from homeassistant.components.climate import PRESET_NONE

from custom_components.tion.presets import TionPresetController

PRESETS = {
    "eco": {"type": "auto", "min_speed": 1, "max_speed": 2},
    "boost": {"type": "manual", "speed": 5},
}


def _controller() -> TionPresetController:
    """Return a controller with one auto and one manual preset."""
    return TionPresetController({name: dict(cfg) for name, cfg in PRESETS.items()})


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


def test_expected_intent_by_type() -> None:
    """Test expected_intent encodes auto as limits and manual as speed."""
    controller = _controller()

    assert controller.expected_intent("eco") == (None, 1, 2)
    assert controller.expected_intent("boost") == (5, None, None)
    assert controller.expected_intent(PRESET_NONE) is None


def test_activate_auto_from_none_saves_current_intent() -> None:
    """Test activating an auto preset saves current intent and returns limits."""
    controller = _controller()

    applied = controller.activate("eco", (3, None, None))

    assert applied == (None, 1, 2)
    assert controller.preset_mode == "eco"
    assert controller.restore_attributes() == {
        "preset_saved_speed": 3,
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }


def test_activate_manual_from_none_saves_current_intent() -> None:
    """Test activating a manual preset saves current intent and returns speed."""
    controller = _controller()

    applied = controller.activate("boost", (None, 1, 4))

    assert applied == (5, None, None)
    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        "preset_saved_speed": None,
        "preset_saved_min_speed": 1,
        "preset_saved_max_speed": 4,
    }


def test_activate_preset_to_preset_keeps_saved() -> None:
    """Test switching preset to preset does not overwrite the saved intent."""
    controller = _controller()
    controller.activate("eco", (3, None, None))

    applied = controller.activate("boost", (None, 1, 2))

    assert applied == (5, None, None)
    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        "preset_saved_speed": 3,
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }


def test_activate_none_restores_saved_intent() -> None:
    """Test returning to PRESET_NONE restores the saved intent and clears it."""
    controller = _controller()
    controller.activate("eco", (3, None, None))

    applied = controller.activate(PRESET_NONE, (None, 1, 2))

    assert applied == (3, None, None)
    assert controller.preset_mode == PRESET_NONE
    assert controller.restore_attributes() == {
        "preset_saved_speed": None,
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }


def test_activate_none_without_saved_returns_current() -> None:
    """Test PRESET_NONE with no saved intent returns the current intent."""
    controller = _controller()

    applied = controller.activate(PRESET_NONE, (2, None, None))

    assert applied == (2, None, None)
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_auto_on_limit_change() -> None:
    """Test an auto preset resets when reported limits diverge."""
    controller = _controller()
    controller.activate("eco", (3, None, None))

    assert controller.reconcile((None, 2, 5)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_auto_on_switch_to_manual() -> None:
    """Test an auto preset resets when the breezer goes manual."""
    controller = _controller()
    controller.activate("eco", (3, None, None))

    assert controller.reconcile((4, None, None)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_manual_on_speed_change() -> None:
    """Test a manual preset resets when the reported speed diverges."""
    controller = _controller()
    controller.activate("boost", (None, 1, 2))

    assert controller.reconcile((3, None, None)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_manual_on_switch_to_auto() -> None:
    """Test a manual preset resets when the breezer goes auto."""
    controller = _controller()
    controller.activate("boost", (None, 1, 2))

    assert controller.reconcile((None, 1, 2)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_keeps_manual_on_limit_change() -> None:
    """Test a manual preset ignores limit changes (not part of its intent)."""
    controller = _controller()
    controller.activate("boost", (None, 1, 2))

    assert controller.reconcile((5, None, None)) is False
    assert controller.preset_mode == "boost"


def test_reconcile_no_reset_when_matches() -> None:
    """Test no reset while the reported intent matches the active preset."""
    controller = _controller()
    controller.activate("eco", (3, None, None))

    assert controller.reconcile((None, 1, 2)) is False
    assert controller.preset_mode == "eco"


def test_restore_rehydrates_active_and_saved() -> None:
    """Test restore sets the active preset and saved intent after a restart."""
    controller = _controller()

    controller.restore("eco", 3, None, None)

    assert controller.preset_mode == "eco"
    assert controller.restore_attributes() == {
        "preset_saved_speed": 3,
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }
    assert controller.reconcile((None, 2, 5)) is True
    assert controller.preset_mode == PRESET_NONE


def test_restore_normalizes_empty_saved_to_none() -> None:
    """Test restore with no saved fields leaves the saved intent cleared."""
    controller = _controller()

    controller.restore("boost", None, None, None)

    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        "preset_saved_speed": None,
        "preset_saved_min_speed": None,
        "preset_saved_max_speed": None,
    }


def test_restore_ignores_unknown_preset() -> None:
    """Test restore ignores a preset name that is not configured."""
    controller = _controller()

    controller.restore("nonexistent", 1, None, None)

    assert controller.preset_mode == PRESET_NONE
