"""Tests for Tion local PID intent value objects."""

from custom_components.tion.client import TionLocation
from custom_components.tion.coordinator import TionData
from custom_components.tion.pid_intent import BreezerCommand, PidIntent, ZoneCommand

BREEZER_GUID = "breezer-guid"


def _data(*, speed: int, is_on: bool) -> TionData:
    """Build coordinator data holding a single breezer."""
    return TionData(
        [
            TionLocation(
                {
                    "guid": "loc",
                    "zones": [
                        {
                            "guid": "zone",
                            "devices": [
                                {
                                    "guid": BREEZER_GUID,
                                    "name": "Breezer",
                                    "data": {"speed": speed, "is_on": is_on},
                                }
                            ],
                        }
                    ],
                }
            )
        ]
    )


def _breezer_command(*, speed: int, is_on: bool) -> BreezerCommand:
    """Build a breezer command with the PID-owned fields."""
    return BreezerCommand(
        guid=BREEZER_GUID,
        is_on=is_on,
        speed=speed,
    )


def test_apply_reflects_breezer_command_on_snapshot() -> None:
    """Test apply optimistically writes the commanded speed and is_on."""
    data = _data(speed=1, is_on=False)
    intent = PidIntent(
        breezer_guid=BREEZER_GUID,
        breezer_command=_breezer_command(speed=6, is_on=True),
    )

    intent.apply(data)

    assert data.device(BREEZER_GUID).data.speed == 6
    assert data.device(BREEZER_GUID).data.is_on is True


def test_apply_is_noop_without_breezer_command() -> None:
    """Test apply leaves the snapshot untouched when only a zone command exists."""
    data = _data(speed=1, is_on=False)
    intent = PidIntent(
        breezer_guid=BREEZER_GUID,
        zone_command=ZoneCommand(guid="zone", co2=800),
    )

    intent.apply(data)

    assert data.device(BREEZER_GUID).data.speed == 1
    assert data.device(BREEZER_GUID).data.is_on is False


def test_apply_is_noop_when_device_missing() -> None:
    """Test apply does not raise when the commanded device is absent."""
    data = _data(speed=1, is_on=False)
    intent = PidIntent(
        breezer_guid="missing",
        breezer_command=_breezer_command(speed=6, is_on=True),
    )

    intent.apply(data)

    assert data.device(BREEZER_GUID).data.speed == 1
