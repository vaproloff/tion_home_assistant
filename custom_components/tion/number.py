"""Settings of a room's auto mode (stations) and of a breezer's local PID."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AutoControl, Breezer, Station
from .const import DOMAIN, PID_NUMBER_KEYS, UNIT_PPM
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, TionRoomAutoEntity, device_views
from .pid_manager import FALLBACK_SPEED_MAX, TionPidManager

MIN_SPEED_KEY, MAX_SPEED_KEY, PID_TARGET_CO2_KEY = PID_NUMBER_KEYS


@dataclass(frozen=True, kw_only=True)
class TionAutoNumberDescription(NumberEntityDescription):
    """A setting of the room's auto mode."""

    field: str
    value_fn: Callable[[AutoControl], int]
    # False when the new value would break speed_min <= speed_max.
    valid_fn: Callable[[AutoControl, int], bool] = lambda auto, value: True


AUTO_SPEED_LIMITS = (
    TionAutoNumberDescription(
        key=MIN_SPEED_KEY,
        translation_key="auto_min_speed",
        field="speed_min",
        value_fn=lambda auto: auto.speed_min,
        valid_fn=lambda auto, value: value <= auto.speed_max,
        native_min_value=0,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    TionAutoNumberDescription(
        key=MAX_SPEED_KEY,
        translation_key="auto_max_speed",
        field="speed_max",
        value_fn=lambda auto: auto.speed_max,
        valid_fn=lambda auto, value: value >= auto.speed_min,
        native_min_value=0,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
)

TARGET_CO2 = TionAutoNumberDescription(
    key="target_co2",
    translation_key="target_co2",
    field="co2_target",
    value_fn=lambda auto: auto.co2_target,
    device_class=NumberDeviceClass.CO2,
    native_unit_of_measurement=UNIT_PPM,
    native_min_value=550,
    native_max_value=1500,
    native_step=10,
    mode=NumberMode.SLIDER,
)


@dataclass(frozen=True, kw_only=True)
class TionPidNumberDescription(NumberEntityDescription):
    """A setting of a breezer's local PID."""

    value_fn: Callable[[TionPidManager, str], int]
    set_fn: Callable[[TionPidManager, str, int], None]


def _limits_error() -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key="speed_limits_invalid"
    )


def _set_pid_min_speed(pid: TionPidManager, breezer_id: str, value: int) -> None:
    if value > pid.limits(breezer_id)[1]:
        raise _limits_error()
    pid.set_limits(breezer_id, speed_min=value)


def _set_pid_max_speed(pid: TionPidManager, breezer_id: str, value: int) -> None:
    if value < pid.limits(breezer_id)[0]:
        raise _limits_error()
    pid.set_limits(breezer_id, speed_max=value)


PID_SPEED_LIMITS = (
    TionPidNumberDescription(
        key=MIN_SPEED_KEY,
        translation_key="pid_min_speed",
        value_fn=lambda pid, breezer_id: pid.limits(breezer_id)[0],
        set_fn=_set_pid_min_speed,
        native_min_value=0,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    TionPidNumberDescription(
        key=MAX_SPEED_KEY,
        translation_key="pid_max_speed",
        value_fn=lambda pid, breezer_id: pid.limits(breezer_id)[1],
        set_fn=_set_pid_max_speed,
        native_min_value=0,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
)

PID_TARGET_CO2 = TionPidNumberDescription(
    key=PID_TARGET_CO2_KEY,
    translation_key="pid_target_co2",
    value_fn=lambda pid, breezer_id: pid.target(breezer_id),
    set_fn=lambda pid, breezer_id, value: pid.set_target(breezer_id, value),
    device_class=NumberDeviceClass.CO2,
    native_unit_of_measurement=UNIT_PPM,
    native_min_value=550,
    native_max_value=1500,
    native_step=10,
    mode=NumberMode.SLIDER,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add auto mode settings to stations in a room, PID settings to breezers."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for device_view in device_views(coordinator):
        if isinstance(device_view, Breezer):
            if coordinator.pid.is_configured(device_view.id):
                entities.extend(
                    TionPidSpeedLimitNumber(coordinator, device_view, description)
                    for description in PID_SPEED_LIMITS
                )
                entities.append(TionPidNumber(coordinator, device_view, PID_TARGET_CO2))
        elif device_view.device.room_id is not None:
            entities.extend(
                TionAutoSpeedLimitNumber(coordinator, device_view, description)
                for description in AUTO_SPEED_LIMITS
            )
            entities.append(TionAutoNumber(coordinator, device_view, TARGET_CO2))
    async_add_entities(entities)


class TionAutoNumber(TionRoomAutoEntity[Station], NumberEntity):
    """A setting of the auto mode of the station's room."""

    entity_description: TionAutoNumberDescription

    def __init__(
        self,
        coordinator: TionCoordinator,
        station: Station,
        description: TionAutoNumberDescription,
    ) -> None:
        """Create the setting for one station."""
        super().__init__(coordinator, station, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the room's setting."""
        if (auto := self.room_auto) is None:
            return None
        return self.entity_description.value_fn(auto)

    async def async_set_native_value(self, value: float) -> None:
        """Change the room's setting."""
        description = self.entity_description
        if (auto := self.room_auto) is not None and not description.valid_fn(
            auto, int(value)
        ):
            raise _limits_error()
        await self.async_set_room_auto(**{description.field: int(value)})


class TionAutoSpeedLimitNumber(TionAutoNumber):
    """A fan speed limit of the room's auto mode, up to its fastest breezer."""

    @property
    def native_max_value(self) -> float:
        """Return the top speed of the fastest breezer in the room."""
        room_id = self.device_view.device.room_id
        return max(
            (
                device_view.speed_max
                for device_view in device_views(self.coordinator)
                if isinstance(device_view, Breezer)
                and device_view.device.room_id == room_id
                and device_view.speed_max is not None
            ),
            default=FALLBACK_SPEED_MAX,
        )


class TionPidNumber(TionEntity[Breezer], NumberEntity):
    """A setting of the breezer's local PID, kept in the entry's options."""

    entity_description: TionPidNumberDescription

    def __init__(
        self,
        coordinator: TionCoordinator,
        breezer: Breezer,
        description: TionPidNumberDescription,
    ) -> None:
        """Create the setting for one breezer."""
        super().__init__(coordinator, breezer, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return True: the setting lives in Home Assistant, not in the cloud."""
        return True

    @property
    def native_value(self) -> float:
        """Return the setting."""
        return self.entity_description.value_fn(
            self.coordinator.pid, self.device_view.id
        )

    async def async_set_native_value(self, value: float) -> None:
        """Save the setting; a running PID applies it at once."""
        self.entity_description.set_fn(
            self.coordinator.pid, self.device_view.id, int(value)
        )
        self.async_write_ha_state()


class TionPidSpeedLimitNumber(TionPidNumber):
    """A fan speed limit of the breezer's local PID, up to its top speed."""

    @property
    def native_max_value(self) -> float:
        """Return the breezer's top speed."""
        return self.device_view.speed_max or FALLBACK_SPEED_MAX
