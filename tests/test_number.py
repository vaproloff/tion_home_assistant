"""Tests for Tion number entities."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tion.const import TionDeviceType
from custom_components.tion.number import TionMaxSpeed, TionMinSpeed, TionTargetCO2

DEVICE_GUID = "device-guid"
ZONE_GUID = "zone-guid"


class FakeReconciler:
    """Fake reconciler recording desired-state writes."""

    def __init__(self) -> None:
        """Initialize empty desired overlays."""
        self.breezer: dict[str, dict[str, Any]] = {}
        self.zone: dict[str, dict[str, Any]] = {}

    def set_breezer(self, guid: str, fields: dict[str, Any]) -> None:
        """Record a breezer desired write."""
        self.breezer.setdefault(guid, {}).update(fields)

    def set_zone(self, guid: str, fields: dict[str, Any]) -> None:
        """Record a zone desired write."""
        self.zone.setdefault(guid, {}).update(fields)

    def reconcile(self, data: Any) -> None:
        """No-op reconcile."""


class FakeCoordinator:
    """Fake Tion coordinator for number tests."""

    def __init__(
        self, device: SimpleNamespace, zone: SimpleNamespace | None = None
    ) -> None:
        """Initialize fake coordinator."""
        self.reconciler = FakeReconciler()
        self.last_update_success = True
        self.data = SimpleNamespace()
        self._device = device
        self._zone = zone

    async def async_request_refresh(self) -> None:
        """Record a refresh request (no-op)."""

    def get_device(self, guid: str) -> SimpleNamespace:
        """Return the fake device."""
        return self._device

    def get_device_zone(self, guid: str) -> SimpleNamespace | None:
        """Return the fake zone."""
        return self._zone


def _device() -> SimpleNamespace:
    """Return a fake breezer device."""
    return SimpleNamespace(
        guid=DEVICE_GUID,
        name="Device",
        type=TionDeviceType.BREEZER_4S,
        is_online=True,
        valid=True,
        max_speed=6,
        data=SimpleNamespace(speed_min_set=1, speed_max_set=4),
    )


def _zone() -> SimpleNamespace:
    """Return a fake zone."""
    return SimpleNamespace(
        guid=ZONE_GUID,
        valid=True,
        mode=SimpleNamespace(auto_set=SimpleNamespace(co2=800)),
    )


def _build(number_cls: type, coordinator: FakeCoordinator) -> Any:
    """Return a number entity bound to the coordinator."""
    entity = number_cls.__new__(number_cls)
    entity.coordinator = coordinator
    entity._device = coordinator._device  # noqa: SLF001
    entity._attr_name = number_cls.__name__  # noqa: SLF001
    entity.async_write_ha_state = lambda: None
    return entity


def test_target_co2_writes_zone_desired() -> None:
    """Test setting target CO2 writes the zone co2 desired."""
    coordinator = FakeCoordinator(_device(), _zone())
    entity = _build(TionTargetCO2, coordinator)

    asyncio.run(entity.async_set_native_value(900))

    assert coordinator.reconciler.zone[ZONE_GUID] == {"co2": 900}


def test_min_speed_writes_breezer_desired() -> None:
    """Test setting the min speed writes the breezer speed_min_set desired."""
    coordinator = FakeCoordinator(_device())
    entity = _build(TionMinSpeed, coordinator)

    asyncio.run(entity.async_set_native_value(2))

    assert coordinator.reconciler.breezer[DEVICE_GUID] == {"speed_min_set": 2}


def test_max_speed_writes_breezer_desired() -> None:
    """Test setting the max speed writes the breezer speed_max_set desired."""
    coordinator = FakeCoordinator(_device())
    entity = _build(TionMaxSpeed, coordinator)

    asyncio.run(entity.async_set_native_value(5))

    assert coordinator.reconciler.breezer[DEVICE_GUID] == {"speed_max_set": 5}


@pytest.mark.parametrize(
    "number_cls",
    [TionMinSpeed, TionMaxSpeed],
    ids=["min_speed", "max_speed"],
)
def test_speed_number_unavailable_raises(number_cls: type) -> None:
    """Test a speed number raises and writes nothing when the device is offline."""
    device = _device()
    device.is_online = False
    coordinator = FakeCoordinator(device)
    entity = _build(number_cls, coordinator)

    with pytest.raises(Exception):  # noqa: B017, PT011
        asyncio.run(entity.async_set_native_value(3))

    assert coordinator.reconciler.breezer == {}
