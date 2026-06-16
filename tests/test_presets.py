"""Tests for the Tion breezer speed preset controller."""

import asyncio

import pytest

from homeassistant.components.climate import FAN_AUTO, PRESET_NONE

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


class _FakeTarget:
    """Minimal PresetTarget for snapshot/apply tests."""

    def __init__(
        self,
        fan_mode: str | None = None,
        speed_min_set: int | None = None,
        speed_max_set: int | None = None,
    ) -> None:
        self.fan_mode = fan_mode
        self.speed_min_set = speed_min_set
        self.speed_max_set = speed_max_set
        self.fan_calls: list[str] = []
        self.auto_calls: list[tuple[int, int]] = []

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self.fan_calls.append(fan_mode)

    async def async_apply_auto_limits(self, min_speed: int, max_speed: int) -> None:
        self.auto_calls.append((min_speed, max_speed))


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


@pytest.mark.parametrize(
    "preset",
    [ManualPreset(3), AutoPreset(1, 4)],
    ids=["manual", "auto"],
)
def test_storage_roundtrip(preset: Preset) -> None:
    """Test to_storage/from_storage round-trips each preset type."""
    assert Preset.from_storage(preset.to_storage()) == preset


def test_from_storage_none() -> None:
    """Test from_storage returns None for a missing saved preset."""
    assert Preset.from_storage(None) is None


def test_equality_across_types() -> None:
    """Test manual and auto presets are never equal even with matching numbers."""
    assert ManualPreset(3) != AutoPreset(3, 3)


def test_snapshot_manual() -> None:
    """Test snapshot reads a manual fan mode as a ManualPreset."""
    assert Preset.snapshot(_FakeTarget(fan_mode="3")) == ManualPreset(3)


def test_snapshot_auto() -> None:
    """Test snapshot reads FAN_AUTO plus limits as an AutoPreset."""
    target = _FakeTarget(fan_mode=FAN_AUTO, speed_min_set=1, speed_max_set=4)

    assert Preset.snapshot(target) == AutoPreset(1, 4)


def test_snapshot_unreadable_returns_none() -> None:
    """Test snapshot returns None when the fan mode or limits are unreadable."""
    assert Preset.snapshot(_FakeTarget(fan_mode=None)) is None
    assert Preset.snapshot(_FakeTarget(fan_mode=FAN_AUTO)) is None


def test_apply_manual_routes_fan_mode() -> None:
    """Test applying a manual preset sets the fan mode and nothing else."""
    target = _FakeTarget()

    asyncio.run(ManualPreset(3).apply(target))

    assert target.fan_calls == ["3"]
    assert target.auto_calls == []


def test_apply_auto_routes_limits() -> None:
    """Test applying an auto preset sets the limits and nothing else."""
    target = _FakeTarget()

    asyncio.run(AutoPreset(1, 4).apply(target))

    assert target.auto_calls == [(1, 4)]
    assert target.fan_calls == []
