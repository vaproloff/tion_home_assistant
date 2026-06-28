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
    PresetBaseline,
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


def _baseline(**overrides: int) -> PresetBaseline:
    """Build an auto baseline carrying the given breezer overrides."""
    return PresetBaseline(overrides=dict(overrides), was_auto=True)


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


def test_managed_fields_union() -> None:
    """Test managed_fields is the union of every preset's desired fields."""
    controller = _controller()

    assert controller.managed_fields == {
        "speed_min_set",
        "speed_max_set",
        "is_on",
        "speed",
    }


def test_activate_saves_baseline_from_passed_overrides() -> None:
    """Test activating a preset saves the supplied baseline (not a live snapshot)."""
    controller = _controller()
    baseline = _baseline(speed_min_set=1, speed_max_set=4)

    controller.activate("eco", baseline)

    assert controller.preset_mode == "eco"
    assert controller.active_preset() == AutoPreset(1, 2)
    assert controller.saved == baseline


def test_repeated_activate_keeps_first_baseline() -> None:
    """Test re-activating while active does not overwrite the saved baseline."""
    controller = _controller()
    first = _baseline(speed_min_set=1, speed_max_set=4)
    controller.activate("eco", first)

    controller.activate("eco", _baseline(speed_min_set=1, speed_max_set=2))

    assert controller.saved == first


def test_activate_preset_to_preset_keeps_saved() -> None:
    """Test switching preset to preset does not overwrite the saved baseline."""
    controller = _controller()
    first = _baseline(speed_min_set=1, speed_max_set=4)
    controller.activate("eco", first)

    controller.activate("boost", _baseline(speed_min_set=1, speed_max_set=2))

    assert controller.preset_mode == "boost"
    assert controller.saved == first


def test_deactivate_clears_active_and_saved() -> None:
    """Test deactivate drops to PRESET_NONE and clears the baseline."""
    controller = _controller()
    controller.activate("eco", _baseline(speed_min_set=1, speed_max_set=4))

    controller.deactivate()

    assert controller.preset_mode == PRESET_NONE
    assert controller.saved is None


def test_restore_rehydrates_active_and_saved() -> None:
    """Test restore sets the active preset and saved baseline after a restart."""
    controller = _controller()
    baseline = _baseline(speed_min_set=1, speed_max_set=4)

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

    controller.restore("nonexistent", _baseline(speed=1))

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
    "baseline",
    [
        PresetBaseline(
            overrides={"speed_min_set": 1, "speed_max_set": 4}, was_auto=True
        ),
        PresetBaseline(overrides={}, was_auto=False),
    ],
    ids=["auto_with_overrides", "manual_empty"],
)
def test_preset_baseline_storage_roundtrip(baseline: PresetBaseline) -> None:
    """Test PresetBaseline round-trips through storage."""
    assert PresetBaseline.from_storage(baseline.to_storage()) == baseline


def test_preset_baseline_from_storage_none() -> None:
    """Test PresetBaseline.from_storage returns None for missing data."""
    assert PresetBaseline.from_storage(None) is None
