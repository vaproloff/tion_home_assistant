"""Tests for the Tion breezer speed presets."""

from typing import Any

import pytest

from custom_components.tion.presets import (
    Baseline,
    ManualPreset,
    PidBaseline,
    PidPreset,
    SpeedBaseline,
    TionPresetController,
    baseline_from_storage,
    baseline_to_storage,
    preset_from_config,
    visible_presets,
)
from homeassistant.components.climate import PRESET_NONE

CONFIGS = {
    "eco": {"type": "local_pid", "min_speed": 1, "max_speed": 2},
    "boost": {"type": "manual", "speed": 5},
    "away": {"type": "auto", "min_speed": 1, "max_speed": 3},
}


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        pytest.param({"type": "manual", "speed": 4}, ManualPreset(4), id="manual"),
        pytest.param(
            {"type": "local_pid", "min_speed": 1, "max_speed": 3},
            PidPreset(1, 3),
            id="local_pid",
        ),
        pytest.param(
            {"type": "auto", "min_speed": 1, "max_speed": 3}, None, id="old_auto"
        ),
        pytest.param({}, None, id="no_type"),
    ],
)
def test_preset_from_config(cfg: dict[str, Any], expected: object) -> None:
    """Options become presets; types this version lacks are skipped."""
    assert preset_from_config(cfg) == expected


@pytest.mark.parametrize(
    ("pid_configured", "expected"),
    [
        pytest.param(False, {"boost": ManualPreset(5)}, id="without_pid"),
        pytest.param(
            True,
            {"eco": PidPreset(1, 2), "boost": ManualPreset(5)},
            id="with_pid",
        ),
    ],
)
def test_visible_presets(pid_configured: bool, expected: dict[str, object]) -> None:
    """PID presets show only while PID is set up; old auto presets never."""
    assert visible_presets(CONFIGS, pid_configured=pid_configured) == expected


@pytest.mark.parametrize(
    "baseline",
    [
        pytest.param(SpeedBaseline(3, True), id="speed_on"),
        pytest.param(SpeedBaseline(None, False), id="unknown_speed_off"),
        pytest.param(PidBaseline(), id="pid"),
    ],
)
def test_baseline_storage_roundtrip(baseline: Baseline) -> None:
    """A baseline survives the restore data."""
    assert baseline_from_storage(baseline_to_storage(baseline)) == baseline


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(None, id="none"),
        pytest.param({}, id="empty"),
        pytest.param({"type": "auto", "min_speed": 1, "max_speed": 3}, id="old_auto"),
        pytest.param({"type": "manual", "speed": "3", "is_on": True}, id="bad_speed"),
        pytest.param({"type": "manual", "speed": 3}, id="no_power"),
    ],
)
def test_baseline_from_storage_drops_unknown(data: dict[str, Any] | None) -> None:
    """Restore data of another shape is dropped instead of failing."""
    assert baseline_from_storage(data) is None


def _controller() -> TionPresetController:
    return TionPresetController({"eco": PidPreset(1, 2), "boost": ManualPreset(5)})


def test_controller_without_presets() -> None:
    """A breezer without presets offers only PRESET_NONE."""
    controller = TionPresetController({})

    assert controller.has_presets is False
    assert controller.preset_modes == [PRESET_NONE]
    assert controller.preset_mode == PRESET_NONE
    assert controller.active_preset() is None


def test_controller_lists_presets() -> None:
    """PRESET_NONE comes first, then the presets in their order."""
    controller = _controller()

    assert controller.has_presets is True
    assert controller.preset_modes == [PRESET_NONE, "eco", "boost"]
    assert controller.preset("boost") == ManualPreset(5)
    assert controller.preset(PRESET_NONE) is None


def test_activate_keeps_first_baseline() -> None:
    """Switching between presets keeps the baseline from before the first one."""
    controller = _controller()

    controller.activate("eco", SpeedBaseline(2, True))
    controller.activate("boost", PidBaseline())

    assert controller.preset_mode == "boost"
    assert controller.active_preset() == ManualPreset(5)
    assert controller.saved == SpeedBaseline(2, True)


def test_deactivate_forgets_baseline() -> None:
    """Leaving the preset clears the active name and the baseline."""
    controller = _controller()
    controller.activate("eco", PidBaseline())

    controller.deactivate()

    assert controller.preset_mode == PRESET_NONE
    assert controller.saved is None


@pytest.mark.parametrize(
    ("active", "expected_mode", "expected_saved"),
    [
        pytest.param("eco", "eco", SpeedBaseline(1, False), id="known"),
        pytest.param("sleep", PRESET_NONE, None, id="unknown"),
    ],
)
def test_restore(
    active: str, expected_mode: str, expected_saved: Baseline | None
) -> None:
    """Restore rehydrates a known preset and ignores an unknown one."""
    controller = _controller()

    controller.restore(active, SpeedBaseline(1, False))

    assert controller.preset_mode == expected_mode
    assert controller.saved == expected_saved
