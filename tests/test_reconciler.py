"""Tests for the Tion desired-state reconciler."""

import asyncio
from types import SimpleNamespace
from typing import Any

from custom_components.tion.client import TionLocation
from custom_components.tion.const import ZoneMode
from custom_components.tion.coordinator import TionData
from custom_components.tion.reconciler import TionReconciler

BREEZER_GUID = "breezer-guid"
ZONE_GUID = "zone"


class _FakeEntry:
    """Config entry double that records background coroutines."""

    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def async_create_background_task(self, hass: Any, coro: Any, name: str) -> Any:
        self.tasks.append(coro)
        return SimpleNamespace(name=name)


class _FakeCoordinator:
    """Coordinator double recording sends in order."""

    def __init__(self, data: TionData) -> None:
        self.data = data
        self.hass = SimpleNamespace()
        self.config_entry = _FakeEntry()
        self.breezer_sends: list[dict[str, Any]] = []
        self.zone_sends: list[dict[str, Any]] = []
        self.order: list[str] = []

    async def async_send_breezer(self, **kwargs: Any) -> bool:
        self.breezer_sends.append(kwargs)
        self.order.append("breezer")
        return True

    async def async_send_zone(self, **kwargs: Any) -> bool:
        self.zone_sends.append(kwargs)
        self.order.append("zone")
        return True


def _data(
    *,
    speed: int = 3,
    zone_mode: ZoneMode = ZoneMode.MANUAL,
    station_online: bool | None = None,
) -> TionData:
    """Build coordinator data with one valid, online breezer in one zone.

    When ``station_online`` is given, the breezer is bound (``zone_hwid``) to a
    MagicAir gateway with that online state, exercising reachability gating.
    """
    breezer: dict[str, Any] = {
        "guid": BREEZER_GUID,
        "name": "Breezer",
        "max_speed": 6,
        "is_online": True,
        "data": {
            "data_valid": True,
            "is_on": True,
            "speed": speed,
            "speed_min_set": 1,
            "speed_max_set": 6,
            "t_set": 20,
            "heater_enabled": False,
            "heater_mode": "maintenance",
            "gate": 0,
        },
    }
    devices: list[dict[str, Any]] = [breezer]
    if station_online is not None:
        breezer["zone_hwid"] = "hw1"
        devices.append(
            {
                "guid": "magicair-guid",
                "name": "MagicAir",
                "type": "co2mb",
                "zone_hwid": "hw1",
                "is_online": station_online,
                "data": {"data_valid": True},
            }
        )
    return TionData(
        [
            TionLocation(
                {
                    "guid": "loc",
                    "zones": [
                        {
                            "guid": ZONE_GUID,
                            "mode": {"current": zone_mode, "auto_set": {"co2": 800}},
                            "devices": devices,
                        }
                    ],
                }
            )
        ]
    )


async def _drain(coordinator: _FakeCoordinator) -> None:
    """Run and clear all scheduled background coroutines."""
    for coro in coordinator.config_entry.tasks:
        await coro
    coordinator.config_entry.tasks.clear()


def test_reconcile_schedules_send_and_optimistically_mutates() -> None:
    """Test a divergent desired field updates the snapshot and schedules a send."""

    async def _run() -> None:
        data = _data(speed=3)
        coordinator = _FakeCoordinator(data)
        reconciler = TionReconciler(coordinator)
        reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

        reconciler.reconcile(data)

        assert data.device(BREEZER_GUID).data.speed == 5  # optimistic
        await _drain(coordinator)
        assert coordinator.breezer_sends[0]["speed"] == 5

    asyncio.run(_run())


def test_reconcile_noop_when_reported_matches() -> None:
    """Test no send is scheduled when desired already matches reported."""
    data = _data(speed=5)
    coordinator = _FakeCoordinator(data)
    reconciler = TionReconciler(coordinator)
    reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

    reconciler.reconcile(data)

    assert coordinator.config_entry.tasks == []


def test_reconcile_inflight_suppresses_duplicate_send() -> None:
    """Test a second reconcile does not double-schedule while a send is in flight."""
    coordinator = _FakeCoordinator(_data(speed=3))
    reconciler = TionReconciler(coordinator)
    reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

    reconciler.reconcile(_data(speed=3))
    reconciler.reconcile(_data(speed=3))  # still in flight, not drained

    assert len(coordinator.config_entry.tasks) == 1
    for coro in coordinator.config_entry.tasks:
        coro.close()


def test_inflight_keeps_optimistic_overlay_on_fresh_fetch() -> None:
    """Test a refresh landing mid-send keeps the optimistic value, not stale cloud.

    A refresh can complete before the cloud reflects an in-flight command. The
    reconciler must re-apply the desired overlay to the fresh snapshot so the
    entity keeps showing the desired value instead of reverting to the stale
    reported one, while not scheduling a duplicate send.
    """
    coordinator = _FakeCoordinator(_data(speed=3))
    reconciler = TionReconciler(coordinator)
    reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

    reconciler.reconcile(_data(speed=3))  # schedules send 5; guid now in flight

    fresh = _data(speed=1)  # refresh landed before the command propagated
    reconciler.reconcile(fresh)

    assert fresh.device(BREEZER_GUID).data.speed == 5  # optimistic value kept
    assert len(coordinator.config_entry.tasks) == 1  # no duplicate send
    for coro in coordinator.config_entry.tasks:
        coro.close()


def test_unconfirmed_field_resends_until_confirmed() -> None:
    """Test an unconfirmed field is re-sent next cycle while still divergent."""

    async def _run() -> None:
        coordinator = _FakeCoordinator(_data(speed=3))
        reconciler = TionReconciler(coordinator)
        reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

        reconciler.reconcile(_data(speed=3))
        await _drain(coordinator)
        reconciler.reconcile(_data(speed=3))  # fresh fetch, cloud still 3
        await _drain(coordinator)

        assert len(coordinator.breezer_sends) == 2

    asyncio.run(_run())


def test_external_change_releases_confirmed_field() -> None:
    """Test a confirmed field that later diverges is dropped (external change)."""

    async def _run() -> None:
        coordinator = _FakeCoordinator(_data(speed=3))
        reconciler = TionReconciler(coordinator)
        reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

        reconciler.reconcile(_data(speed=3))  # schedule 5
        await _drain(coordinator)
        reconciler.reconcile(_data(speed=5))  # cloud now 5 -> confirmed
        reconciler.reconcile(_data(speed=2))  # external change -> release

        assert coordinator.config_entry.tasks == []  # nothing re-sent

    asyncio.run(_run())


def test_external_change_releases_only_diverged_field() -> None:
    """Test an external change to one field drops only it, keeping other intents."""

    async def _run() -> None:
        coordinator = _FakeCoordinator(_data(speed=3))
        reconciler = TionReconciler(coordinator)
        reconciler.set_breezer(BREEZER_GUID, {"speed": 5, "t_set": 22})

        reconciler.reconcile(_data(speed=3))  # send both (pending)
        await _drain(coordinator)
        confirmed = _data(speed=5)
        confirmed.device(BREEZER_GUID).data.t_set = 22
        reconciler.reconcile(confirmed)  # both confirmed
        external = _data(speed=2)
        external.device(BREEZER_GUID).data.t_set = 22
        reconciler.reconcile(external)  # speed diverged -> release speed only

        desired = reconciler._breezers[BREEZER_GUID]  # noqa: SLF001
        assert "speed" not in desired
        assert desired["t_set"] == 22

    asyncio.run(_run())


def test_zone_precondition_sent_before_breezer_in_one_task() -> None:
    """Test zone MANUAL precondition is sent before the breezer speed, in order."""

    async def _run() -> None:
        data = _data(speed=3, zone_mode=ZoneMode.AUTO)
        coordinator = _FakeCoordinator(data)
        reconciler = TionReconciler(coordinator)
        reconciler.set_zone(ZONE_GUID, {"mode": ZoneMode.MANUAL})
        reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

        reconciler.reconcile(data)
        await _drain(coordinator)

        assert coordinator.order == ["zone", "breezer"]

    asyncio.run(_run())


def test_current_breezer_returns_copy_of_desired() -> None:
    """Test current_breezer returns a copy that cannot mutate internal state."""
    coordinator = _FakeCoordinator(_data(speed=3))
    reconciler = TionReconciler(coordinator)
    reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

    snap = reconciler.current_breezer(BREEZER_GUID)
    assert snap == {"speed": 5}

    snap["speed"] = 9
    assert reconciler.current_breezer(BREEZER_GUID) == {"speed": 5}


def test_holds_true_only_when_fields_present_with_matching_values() -> None:
    """Test holds requires every field present AND unchanged in value.

    Overwriting a managed field with a different value (e.g. the number entity
    changing max speed) must release the hold, not just dropping the key.
    """
    coordinator = _FakeCoordinator(_data(speed=3))
    reconciler = TionReconciler(coordinator)
    fields = {"speed_min_set": 1, "speed_max_set": 2}
    reconciler.set_breezer(BREEZER_GUID, dict(fields))

    assert reconciler.holds(BREEZER_GUID, fields) is True

    # Value overwritten -> no longer the preset's value -> not held.
    reconciler.set_breezer(BREEZER_GUID, {"speed_max_set": 3})
    assert reconciler.holds(BREEZER_GUID, fields) is False

    # Field dropped entirely -> also not held.
    reconciler.release(BREEZER_GUID, ["speed_max_set"])
    assert reconciler.holds(BREEZER_GUID, fields) is False


def test_release_drops_fields_and_confirmed() -> None:
    """Test release removes fields from the desired overlay."""
    coordinator = _FakeCoordinator(_data(speed=3))
    reconciler = TionReconciler(coordinator)
    reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

    reconciler.release(BREEZER_GUID, ["speed"])

    assert reconciler.current_breezer(BREEZER_GUID) == {}


def test_zone_only_desired_is_dispatched() -> None:
    """Test a zone-only desire (cloud auto, no breezer write) still sends."""

    async def _run() -> None:
        data = _data(speed=3, zone_mode=ZoneMode.MANUAL)
        coordinator = _FakeCoordinator(data)
        reconciler = TionReconciler(coordinator)
        reconciler.set_zone(ZONE_GUID, {"mode": ZoneMode.AUTO})

        reconciler.reconcile(data)
        await _drain(coordinator)

        assert coordinator.zone_sends[0]["mode"] == ZoneMode.AUTO
        assert coordinator.breezer_sends == []

    asyncio.run(_run())


def test_reconcile_skips_breezer_when_gateway_offline() -> None:
    """Test no command is dispatched (nor optimistic apply) when unreachable."""
    data = _data(speed=3, station_online=False)
    coordinator = _FakeCoordinator(data)
    reconciler = TionReconciler(coordinator)
    reconciler.set_breezer(BREEZER_GUID, {"speed": 5})

    reconciler.reconcile(data)

    assert coordinator.config_entry.tasks == []
    assert data.device(BREEZER_GUID).data.speed == 3  # no optimistic overwrite


def test_reconcile_zone_only_skipped_when_gateway_offline() -> None:
    """Test a zone-only command is not dispatched when the zone gateway is down."""
    data = _data(zone_mode=ZoneMode.MANUAL, station_online=False)
    coordinator = _FakeCoordinator(data)
    reconciler = TionReconciler(coordinator)
    reconciler.set_zone(ZONE_GUID, {"mode": ZoneMode.AUTO})

    reconciler.reconcile(data)

    assert coordinator.config_entry.tasks == []
