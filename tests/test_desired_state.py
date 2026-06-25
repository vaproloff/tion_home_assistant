"""Tests for Tion desired-state value objects."""

from custom_components.tion.client import TionLocation, TionZone, TionZoneDevice
from custom_components.tion.const import ZoneMode
from custom_components.tion.coordinator import TionData
from custom_components.tion.desired_state import DesiredBreezer, DesiredZone

BREEZER_GUID = "breezer-guid"


def _data() -> TionData:
    """Build coordinator data with a single valid, online breezer."""
    return TionData(
        [
            TionLocation(
                {
                    "guid": "loc",
                    "zones": [
                        {
                            "guid": "zone",
                            "mode": {
                                "current": ZoneMode.MANUAL,
                                "auto_set": {"co2": 800},
                            },
                            "devices": [
                                {
                                    "guid": BREEZER_GUID,
                                    "name": "Breezer",
                                    "max_speed": 6,
                                    "is_online": True,
                                    "data": {
                                        "data_valid": True,
                                        "is_on": True,
                                        "speed": 3,
                                        "speed_min_set": 1,
                                        "speed_max_set": 6,
                                        "t_set": 20,
                                        "heater_enabled": False,
                                        "heater_mode": "maintenance",
                                        "gate": 0,
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
        ]
    )


def _device() -> TionZoneDevice:
    """Return the single reported breezer."""
    device = _data().device(BREEZER_GUID)
    assert device is not None
    return device


def _zone() -> TionZone:
    """Return the single reported zone."""
    zone = _data().zone(BREEZER_GUID)
    assert zone is not None
    return zone


def test_breezer_merge_overlays_only_specified_fields() -> None:
    """Test merge keeps reported fields and overlays only the desired ones."""
    payload = DesiredBreezer({"speed": 5}).merge(_device())

    assert payload == {
        "guid": BREEZER_GUID,
        "is_on": True,
        "speed": 5,
        "t_set": 20,
        "speed_min_set": 1,
        "speed_max_set": 6,
        "heater_enabled": False,
        "heater_mode": "maintenance",
        "gate": 0,
    }


def test_breezer_merge_preserves_explicit_none() -> None:
    """Test a key set to None overrides reported (distinct from an absent key)."""
    payload = DesiredBreezer({"heater_enabled": None}).merge(_device())

    assert payload is not None
    assert payload["heater_enabled"] is None


def test_breezer_diff_true_when_field_differs() -> None:
    """Test diff is True when a desired field differs from reported."""
    assert DesiredBreezer({"speed": 5}).diff(_device()) is True


def test_breezer_diff_false_when_matches_reported() -> None:
    """Test diff is False when the desired field already matches reported."""
    assert DesiredBreezer({"speed": 3}).diff(_device()) is False


def test_breezer_merge_none_on_invalid_reported() -> None:
    """Test merge returns None when a required numeric field is unreadable."""
    device = _device()
    device.data.t_set = None

    assert DesiredBreezer({"speed": 5}).merge(device) is None


def test_zone_merge_overlays_mode_and_keeps_co2() -> None:
    """Test zone merge overlays mode and falls back to reported co2."""
    payload = DesiredZone({"mode": ZoneMode.AUTO}).merge(_zone())

    assert payload == {"guid": "zone", "mode": ZoneMode.AUTO, "co2": 800}


def test_zone_diff_true_when_mode_differs() -> None:
    """Test zone diff is True when the desired mode differs from reported."""
    assert DesiredZone({"mode": ZoneMode.AUTO}).diff(_zone()) is True


def test_zone_diff_false_when_mode_matches() -> None:
    """Test zone diff is False when the desired mode already matches reported."""
    assert DesiredZone({"mode": ZoneMode.MANUAL}).diff(_zone()) is False
