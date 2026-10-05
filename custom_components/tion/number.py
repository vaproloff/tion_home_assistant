"""Room auto mode settings of Tion breezers and stations."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AutoControl, Breezer, Station
from .const import UNIT_PPM
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionRoomAutoEntity, device_views


@dataclass(frozen=True, kw_only=True)
class TionNumberDescription(NumberEntityDescription):
    """A setting of the room's auto mode."""

    field: str
    value_fn: Callable[[AutoControl], int]


SPEED_LIMITS = (
    TionNumberDescription(
        key="min_speed_set",
        translation_key="min_speed_set",
        field="speed_min",
        value_fn=lambda auto: auto.speed_min,
        native_min_value=0,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    TionNumberDescription(
        key="max_speed_set",
        translation_key="max_speed_set",
        field="speed_max",
        value_fn=lambda auto: auto.speed_max,
        native_min_value=0,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
)

TARGET_CO2 = TionNumberDescription(
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the auto mode settings to the devices that are in a room."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for device_view in device_views(coordinator):
        if device_view.device.room_id is None:
            continue
        if isinstance(device_view, Breezer):
            entities.extend(
                TionSpeedLimitNumber(coordinator, device_view, description)
                for description in SPEED_LIMITS
            )
        else:
            entities.append(TionAutoNumber(coordinator, device_view, TARGET_CO2))
    async_add_entities(entities)


class TionAutoNumber[ViewT: Breezer | Station](TionRoomAutoEntity[ViewT], NumberEntity):
    """A setting of the auto mode of the device's room, shared by its devices."""

    entity_description: TionNumberDescription

    def __init__(
        self,
        coordinator: TionCoordinator,
        device_view: ViewT,
        description: TionNumberDescription,
    ) -> None:
        """Create the setting for one device."""
        super().__init__(coordinator, device_view, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the room's setting."""
        if (auto := self.room_auto) is None:
            return None
        return self.entity_description.value_fn(auto)

    async def async_set_native_value(self, value: float) -> None:
        """Change the room's setting."""
        await self.async_set_room_auto(**{self.entity_description.field: int(value)})


class TionSpeedLimitNumber(TionAutoNumber[Breezer]):
    """A fan speed limit of the room's auto mode, up to the breezer's top speed."""

    @property
    def native_max_value(self) -> float:
        """Return the breezer's top speed."""
        if (speed_max := self.device_view.speed_max) is None:
            return super().native_max_value
        return speed_max
