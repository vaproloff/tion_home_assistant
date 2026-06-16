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
    ATTR_SAVED_PRESET,
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


def test_preset_returns_configured_object() -> None:
    """Test preset() returns the configured Preset object by name."""
    controller = _controller()

    assert controller.preset("eco") == AutoPreset(1, 2)
    assert controller.preset("boost") == ManualPreset(5)
    assert controller.preset(PRESET_NONE) is None


def test_activate_auto_from_none_saves_current() -> None:
    """Test activating an auto preset saves current state and returns its limits."""
    controller = _controller()

    applied = controller.activate("eco", ManualPreset(3))

    assert applied == AutoPreset(1, 2)
    assert controller.preset_mode == "eco"
    assert controller.restore_attributes() == {
        ATTR_SAVED_PRESET: {"type": "manual", "speed": 3}
    }


def test_activate_manual_from_none_saves_current() -> None:
    """Test activating a manual preset saves current state and returns its speed."""
    controller = _controller()

    applied = controller.activate("boost", AutoPreset(1, 4))

    assert applied == ManualPreset(5)
    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        ATTR_SAVED_PRESET: {"type": "auto", "min_speed": 1, "max_speed": 4}
    }


def test_activate_preset_to_preset_keeps_saved() -> None:
    """Test switching preset to preset does not overwrite the saved state."""
    controller = _controller()
    controller.activate("eco", ManualPreset(3))

    applied = controller.activate("boost", AutoPreset(1, 2))

    assert applied == ManualPreset(5)
    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {
        ATTR_SAVED_PRESET: {"type": "manual", "speed": 3}
    }


def test_activate_none_restores_saved() -> None:
    """Test returning to PRESET_NONE restores the saved state and clears it."""
    controller = _controller()
    controller.activate("eco", ManualPreset(3))

    applied = controller.activate(PRESET_NONE, AutoPreset(1, 2))

    assert applied == ManualPreset(3)
    assert controller.preset_mode == PRESET_NONE
    assert controller.restore_attributes() == {ATTR_SAVED_PRESET: None}


def test_activate_none_without_saved_returns_current() -> None:
    """Test PRESET_NONE with no saved state returns the current state."""
    controller = _controller()

    applied = controller.activate(PRESET_NONE, ManualPreset(2))

    assert applied == ManualPreset(2)
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_auto_on_limit_change() -> None:
    """Test an auto preset resets when reported limits diverge."""
    controller = _controller()
    controller.activate("eco", ManualPreset(3))

    assert controller.reconcile(AutoPreset(2, 5)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_auto_on_switch_to_manual() -> None:
    """Test an auto preset resets when the breezer goes manual."""
    controller = _controller()
    controller.activate("eco", ManualPreset(3))

    assert controller.reconcile(ManualPreset(4)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_manual_on_speed_change() -> None:
    """Test a manual preset resets when the reported speed diverges."""
    controller = _controller()
    controller.activate("boost", AutoPreset(1, 2))

    assert controller.reconcile(ManualPreset(3)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_resets_manual_on_switch_to_auto() -> None:
    """Test a manual preset resets when the breezer goes auto."""
    controller = _controller()
    controller.activate("boost", AutoPreset(1, 2))

    assert controller.reconcile(AutoPreset(1, 2)) is True
    assert controller.preset_mode == PRESET_NONE


def test_reconcile_keeps_manual_on_limit_change() -> None:
    """Test a manual preset ignores limit changes (not part of its intent)."""
    controller = _controller()
    controller.activate("boost", AutoPreset(1, 2))

    assert controller.reconcile(ManualPreset(5)) is False
    assert controller.preset_mode == "boost"


def test_reconcile_no_reset_when_matches() -> None:
    """Test no reset while the reported state matches the active preset."""
    controller = _controller()
    controller.activate("eco", ManualPreset(3))

    assert controller.reconcile(AutoPreset(1, 2)) is False
    assert controller.preset_mode == "eco"


def test_reconcile_none_does_not_reset() -> None:
    """Test an unreadable snapshot does not reset the active preset."""
    controller = _controller()
    controller.activate("eco", ManualPreset(3))

    assert controller.reconcile(None) is False
    assert controller.preset_mode == "eco"


def test_restore_rehydrates_active_and_saved() -> None:
    """Test restore sets the active preset and saved state after a restart."""
    controller = _controller()

    controller.restore("eco", ManualPreset(3))

    assert controller.preset_mode == "eco"
    assert controller.restore_attributes() == {
        ATTR_SAVED_PRESET: {"type": "manual", "speed": 3}
    }
    assert controller.reconcile(AutoPreset(2, 5)) is True
    assert controller.preset_mode == PRESET_NONE


def test_restore_without_saved_clears_saved() -> None:
    """Test restore with no saved preset leaves the saved state cleared."""
    controller = _controller()

    controller.restore("boost", None)

    assert controller.preset_mode == "boost"
    assert controller.restore_attributes() == {ATTR_SAVED_PRESET: None}


def test_restore_ignores_unknown_preset() -> None:
    """Test restore ignores a preset name that is not configured."""
    controller = _controller()

    controller.restore("nonexistent", ManualPreset(1))

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
