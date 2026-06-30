"""Tests for the Tion breezer speed preset controller."""

import pytest

from custom_components.tion.const import (
    CONF_PRESET_MAX_SPEED,
    CONF_PRESET_MIN_SPEED,
    CONF_PRESET_SPEED,
    CONF_PRESET_TYPE,
    TionPresetType,
)
from custom_components.tion.presets import (
    AutoPreset,
    ManualPreset,
    Preset,
    TionPresetController,
)
from homeassistant.components.climate import PRESET_NONE

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


def test_preset_returns_configured_object() -> None:
    """Test preset() returns the configured Preset object by name."""
    controller = _controller()

    assert controller.preset("eco") == AutoPreset(1, 2)
    assert controller.preset("boost") == ManualPreset(5)
    assert controller.preset(PRESET_NONE) is None


def test_auto_preset_desired_fields_and_mode() -> None:
    """Test an auto preset overlays speed limits and runs in auto."""
    assert AutoPreset(1, 2).desired_fields() == {"speed_min_set": 1, "speed_max_set": 2}
    assert AutoPreset(1, 2).is_auto() is True


def test_manual_preset_desired_fields_and_mode() -> None:
    """Test a manual preset overlays on/speed and does not run in auto."""
    assert ManualPreset(3).desired_fields() == {"is_on": True, "speed": 3}
    assert ManualPreset(3).is_auto() is False


def test_manual_preset_off_overlays_power_state() -> None:
    """Test a manual baseline preset carries a real (off) power state."""
    assert ManualPreset(3, is_on=False).desired_fields() == {
        "is_on": False,
        "speed": 3,
    }


def test_managed_fields_union() -> None:
    """Test managed_fields is the union of every preset's desired fields."""
    controller = _controller()

    assert controller.managed_fields == {
        "speed_min_set",
        "speed_max_set",
        "is_on",
        "speed",
    }


def test_activate_saves_baseline_from_passed_preset() -> None:
    """Test activating a preset saves the supplied baseline preset."""
    controller = _controller()
    baseline = AutoPreset(1, 4)

    controller.activate("eco", baseline)

    assert controller.preset_mode == "eco"
    assert controller.active_preset() == AutoPreset(1, 2)
    assert controller.saved == baseline


def test_repeated_activate_keeps_first_baseline() -> None:
    """Test re-activating while active does not overwrite the saved baseline."""
    controller = _controller()
    first = AutoPreset(1, 4)
    controller.activate("eco", first)

    controller.activate("eco", AutoPreset(1, 2))

    assert controller.saved == first


def test_activate_preset_to_preset_keeps_saved() -> None:
    """Test switching preset to preset does not overwrite the saved baseline."""
    controller = _controller()
    first = AutoPreset(1, 4)
    controller.activate("eco", first)

    controller.activate("boost", AutoPreset(1, 2))

    assert controller.preset_mode == "boost"
    assert controller.saved == first


def test_deactivate_clears_active_and_saved() -> None:
    """Test deactivate drops to PRESET_NONE and clears the baseline."""
    controller = _controller()
    controller.activate("eco", AutoPreset(1, 4))

    controller.deactivate()

    assert controller.preset_mode == PRESET_NONE
    assert controller.saved is None


def test_restore_rehydrates_active_and_saved() -> None:
    """Test restore sets the active preset and saved baseline after a restart."""
    controller = _controller()
    baseline = AutoPreset(1, 4)

    controller.restore("eco", baseline)

    assert controller.preset_mode == "eco"
    assert controller.saved == baseline


def test_restore_without_saved_clears_saved() -> None:
    """Test restore with no saved baseline leaves the saved state cleared."""
    controller = _controller()

    controller.restore("boost", None)

    assert controller.preset_mode == "boost"
    assert controller.saved is None


def test_restore_ignores_unknown_preset() -> None:
    """Test restore ignores a preset name that is not configured."""
    controller = _controller()

    controller.restore("nonexistent", ManualPreset(1))

    assert controller.preset_mode == PRESET_NONE


def test_from_config_manual() -> None:
    """Test from_config builds a ManualPreset for the manual type."""
    preset = Preset.from_config(
        {CONF_PRESET_TYPE: TionPresetType.MANUAL.value, CONF_PRESET_SPEED: 3}
    )

    assert preset == ManualPreset(3)


def test_from_config_auto() -> None:
    """Test from_config builds an AutoPreset for the auto type."""
    preset = Preset.from_config(
        {
            CONF_PRESET_TYPE: TionPresetType.AUTO.value,
            CONF_PRESET_MIN_SPEED: 1,
            CONF_PRESET_MAX_SPEED: 4,
        }
    )

    assert preset == AutoPreset(1, 4)


def test_equality_across_types() -> None:
    """Test manual and auto presets are never equal even with matching numbers."""
    assert ManualPreset(3) != AutoPreset(3, 3)


@pytest.mark.parametrize(
    "preset",
    [
        AutoPreset(1, 4),
        ManualPreset(3),
        ManualPreset(0, is_on=False),
    ],
    ids=["auto", "manual_on", "manual_off"],
)
def test_preset_storage_roundtrip(preset: Preset) -> None:
    """Test a preset round-trips through storage."""
    assert Preset.from_storage(preset.to_storage()) == preset


def test_preset_from_storage_none() -> None:
    """Test Preset.from_storage returns None for missing data."""
    assert Preset.from_storage(None) is None
