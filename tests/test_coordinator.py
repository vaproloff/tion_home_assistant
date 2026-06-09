"""Tests for the Tion data update coordinator."""

# FakeClient, FakePidManager, _location, and _make_coordinator form a shared
# test harness used across coordinator tests added in multiple tasks.  Task 1
# exercises the track_stale guard; Tasks 2 and 4 extend coverage to
# get_device(..., data=...) and _async_update_data respectively, and rely on
# this same harness.  Do not remove unused helpers — they are intentional.

import asyncio
from types import SimpleNamespace

from custom_components.tion.client import TionLocation
from custom_components.tion.coordinator import TionData, TionDataUpdateCoordinator

BREEZER_GUID = "breezer-guid"


class FakeClient:
    """Fake Tion API client."""

    def __init__(self, locations: list[TionLocation]) -> None:
        """Initialize fake client."""
        self._locations = locations

    async def get_locations(self) -> list[TionLocation]:
        """Return canned locations."""
        return self._locations


class FakePidManager:
    """Fake local PID manager for coordinator tests."""

    def __init__(self, *, active: bool) -> None:
        """Initialize fake PID manager."""
        self._active = active
        self.evaluated: TionData | None = None

    def has_active_pid(self) -> bool:
        """Return whether any PID controller is active."""
        return self._active

    async def async_evaluate_all(self, data: TionData) -> None:
        """Record the data PID was evaluated on."""
        self.evaluated = data


def _location(*, speed: int) -> TionLocation:
    """Build a location holding a single breezer with the given speed."""
    return TionLocation(
        {
            "guid": "loc",
            "zones": [
                {
                    "guid": "zone",
                    "devices": [
                        {
                            "guid": BREEZER_GUID,
                            "name": "Breezer",
                            "data": {"speed": speed, "data_valid": True},
                        }
                    ],
                }
            ],
        }
    )


def _make_coordinator(
    *,
    client: FakeClient,
    data: TionData | None = None,
    pid_manager: FakePidManager | None = None,
    current_started: float | None = None,
    last_completed: float | None = None,
    now: float = 100.0,
) -> TionDataUpdateCoordinator:
    """Build a coordinator instance without running DataUpdateCoordinator.__init__."""
    coordinator = object.__new__(TionDataUpdateCoordinator)
    coordinator.hass = SimpleNamespace(loop=SimpleNamespace(time=lambda: now))
    coordinator.client = client
    coordinator.data = data
    coordinator.pid_manager = pid_manager or FakePidManager(active=False)
    coordinator._current_command_started_at = current_started  # noqa: SLF001
    coordinator._last_command_completed_at = last_completed  # noqa: SLF001
    return coordinator


def test_send_command_track_stale_false_keeps_timestamps() -> None:
    """Test PID commands (track_stale=False) do not touch stale timestamps."""
    coordinator = _make_coordinator(client=FakeClient([]), now=50.0)

    async def _ok() -> bool:
        return True

    result = asyncio.run(
        coordinator._async_send_command(  # noqa: SLF001
            _ok(), request_refresh=False, track_stale=False
        )
    )

    assert result is True
    assert coordinator._current_command_started_at is None  # noqa: SLF001
    assert coordinator._last_command_completed_at is None  # noqa: SLF001


def test_send_command_track_stale_false_leaves_inflight_marker() -> None:
    """Test PID command does not clear an in-flight manual command marker.

    When a manual command is already in-flight (_current_command_started_at is
    set) and a concurrent track_stale=False (PID) command completes, the
    in-flight marker must be left untouched so the stale-data guard remains
    intact for the manual command.
    """
    coordinator = _make_coordinator(
        client=FakeClient([]), now=100.0, current_started=99.0
    )

    async def _ok() -> bool:
        return True

    result = asyncio.run(
        coordinator._async_send_command(  # noqa: SLF001
            _ok(), request_refresh=False, track_stale=False
        )
    )

    assert result is True
    assert coordinator._current_command_started_at == 99.0  # noqa: SLF001
    assert coordinator._last_command_completed_at is None  # noqa: SLF001


def test_send_command_track_stale_true_marks_completion() -> None:
    """Test manual commands (track_stale=True) mark completion time."""
    coordinator = _make_coordinator(client=FakeClient([]), now=50.0)

    async def _ok() -> bool:
        return True

    result = asyncio.run(
        coordinator._async_send_command(  # noqa: SLF001
            _ok(), request_refresh=False, track_stale=True
        )
    )

    assert result is True
    assert coordinator._last_command_completed_at == 50.0  # noqa: SLF001
    assert coordinator._current_command_started_at is None  # noqa: SLF001


def test_get_device_prefers_passed_data() -> None:
    """Test get_device reads from the passed data, else falls back to self.data."""
    own = TionData([_location(speed=1)])
    fresh = TionData([_location(speed=9)])
    coordinator = _make_coordinator(client=FakeClient([]), data=own)

    assert coordinator.get_device(BREEZER_GUID).data.speed == 1
    assert coordinator.get_device(BREEZER_GUID, fresh).data.speed == 9


def test_get_device_zone_prefers_passed_data() -> None:
    """Test get_device_zone resolves the zone from the passed data."""
    own = TionData([_location(speed=1)])
    fresh = TionData([_location(speed=9)])
    coordinator = _make_coordinator(client=FakeClient([]), data=own)

    assert coordinator.get_device_zone(BREEZER_GUID, fresh).guid == "zone"
    assert coordinator.get_device_zone(BREEZER_GUID).guid == "zone"
