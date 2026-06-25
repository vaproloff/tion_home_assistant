"""Tests for the Tion data update coordinator."""

# FakeClient, FakePidManager, FakeReconciler, _location, and _make_coordinator
# form a shared test harness covering the coordinator's stale-command guard, the
# TionData lookup helpers, and desired-state reconciliation inside
# _async_update_data.  Do not remove unused helpers — they are intentional.

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
        self.written: TionData | None = None

    def has_active_pid(self) -> bool:
        """Return whether any PID controller is active."""
        return self._active

    def write_all(self, data: TionData) -> None:
        """Record the data PID was asked to write desired state on."""
        self.written = data


class FakeReconciler:
    """Fake reconciler recording the data it reconciled."""

    def __init__(self) -> None:
        """Initialize fake reconciler."""
        self.reconciled: TionData | None = None

    def reconcile(self, data: TionData) -> None:
        """Record the reconciled snapshot."""
        self.reconciled = data


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
    reconciler: FakeReconciler | None = None,
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
    coordinator.reconciler = reconciler or FakeReconciler()
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
    """Test a track_stale=False command does not clear an in-flight manual marker."""
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


def test_tion_data_devices_returns_all_devices() -> None:
    """Test TionData.devices() flattens devices across locations and zones."""
    data = TionData([_location(speed=5)])

    assert [device.guid for device in data.devices()] == [BREEZER_GUID]


def test_tion_data_device_finds_device_by_guid() -> None:
    """Test TionData.device() returns the matching device, else None."""
    data = TionData([_location(speed=5)])

    assert data.device(BREEZER_GUID).data.speed == 5
    assert data.device("missing") is None


def test_tion_data_zone_finds_zone_by_device_guid() -> None:
    """Test TionData.zone() returns the zone containing the device, else None."""
    data = TionData([_location(speed=5)])

    assert data.zone(BREEZER_GUID).guid == "zone"
    assert data.zone("missing") is None


def test_get_device_delegates_to_data() -> None:
    """Test get_device resolves the device from self.data."""
    coordinator = _make_coordinator(
        client=FakeClient([]), data=TionData([_location(speed=1)])
    )

    assert coordinator.get_device(BREEZER_GUID).data.speed == 1


def test_get_device_zone_delegates_to_data() -> None:
    """Test get_device_zone resolves the zone from self.data."""
    coordinator = _make_coordinator(
        client=FakeClient([]), data=TionData([_location(speed=1)])
    )

    assert coordinator.get_device_zone(BREEZER_GUID).guid == "zone"


def test_update_writes_desired_and_reconciles_when_active() -> None:
    """Test active PID writes desired state and the reconciler runs on fresh data."""
    pid_manager = FakePidManager(active=True)
    reconciler = FakeReconciler()
    coordinator = _make_coordinator(
        client=FakeClient([_location(speed=3)]),
        pid_manager=pid_manager,
        reconciler=reconciler,
    )

    result = asyncio.run(coordinator._async_update_data())  # noqa: SLF001

    assert pid_manager.written is result
    assert reconciler.reconciled is result


def test_apply_desired_writes_pid_then_reconciles_when_active() -> None:
    """Test apply_desired recomputes active PID before reconciling the snapshot."""
    pid_manager = FakePidManager(active=True)
    reconciler = FakeReconciler()
    coordinator = _make_coordinator(
        client=FakeClient([]), pid_manager=pid_manager, reconciler=reconciler
    )
    data = TionData([_location(speed=1)])

    coordinator.apply_desired(data)

    assert pid_manager.written is data
    assert reconciler.reconciled is data


def test_apply_desired_skips_pid_when_inactive() -> None:
    """Test apply_desired reconciles without recomputing PID when inactive."""
    pid_manager = FakePidManager(active=False)
    reconciler = FakeReconciler()
    coordinator = _make_coordinator(
        client=FakeClient([]), pid_manager=pid_manager, reconciler=reconciler
    )
    data = TionData([_location(speed=1)])

    coordinator.apply_desired(data)

    assert pid_manager.written is None
    assert reconciler.reconciled is data


def test_update_reconciles_but_skips_pid_when_inactive() -> None:
    """Test the reconciler always runs, but PID does not write when inactive."""
    pid_manager = FakePidManager(active=False)
    reconciler = FakeReconciler()
    coordinator = _make_coordinator(
        client=FakeClient([_location(speed=3)]),
        pid_manager=pid_manager,
        reconciler=reconciler,
    )

    result = asyncio.run(coordinator._async_update_data())  # noqa: SLF001

    assert pid_manager.written is None
    assert reconciler.reconciled is result


def test_update_returns_cached_data_and_skips_reconcile_when_stale() -> None:
    """Test stale data (recent manual command) is returned, PID and reconcile skipped."""
    cached = TionData([_location(speed=1)])
    pid_manager = FakePidManager(active=True)
    reconciler = FakeReconciler()
    coordinator = _make_coordinator(
        client=FakeClient([_location(speed=9)]),
        data=cached,
        pid_manager=pid_manager,
        reconciler=reconciler,
        now=100.0,
        last_completed=200.0,
    )

    result = asyncio.run(coordinator._async_update_data())  # noqa: SLF001

    assert result is cached
    assert pid_manager.written is None
    assert reconciler.reconciled is None


def test_update_accepts_cloud_data_after_command_completed_before_fetch() -> None:
    """Test a completed command does not stale future fetches."""
    cached = TionData([_location(speed=6)])
    coordinator = _make_coordinator(
        client=FakeClient([_location(speed=1)]),
        data=cached,
        now=101.0,
        last_completed=100.0,
    )

    result = asyncio.run(coordinator._async_update_data())  # noqa: SLF001

    assert result is not cached
    assert result.device(BREEZER_GUID).data.speed == 1
