"""Sensors of Tion breezers and stations."""

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import Breezer, Station
from .const import UNIT_PPM, UNIT_UG_PER_M3
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, device_views

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, kw_only=True)
class TionSensorDescription[ViewT: Breezer | Station](SensorEntityDescription):
    """A sensor reading one view property the model supports."""

    feature: str
    value_fn: Callable[[ViewT], StateType]


def _filter_days(breezer: Breezer) -> int | None:
    if (remaining := breezer.filter_remaining) is None:
        return None
    return max(0, ceil(remaining / SECONDS_PER_DAY))


BREEZER_SENSORS: tuple[TionSensorDescription[Breezer], ...] = (
    TionSensorDescription(
        key="temperature_in",
        translation_key="temperature_in",
        feature="temperature_outdoor",
        value_fn=lambda breezer: breezer.temperature_outdoor,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=0,
    ),
    TionSensorDescription(
        key="temperature_out",
        translation_key="temperature_out",
        feature="temperature_outlet",
        value_fn=lambda breezer: breezer.temperature_outlet,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=0,
    ),
    TionSensorDescription(
        key="filter_replacement_days",
        translation_key="filter_replacement_days",
        feature="filter_remaining",
        value_fn=_filter_days,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
    ),
)

STATION_SENSORS: tuple[TionSensorDescription[Station], ...] = (
    TionSensorDescription(
        key="temperature",
        translation_key="temperature",
        feature="temperature",
        value_fn=lambda station: station.temperature,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    TionSensorDescription(
        key="humidity",
        translation_key="humidity",
        feature="humidity",
        value_fn=lambda station: station.humidity,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
    ),
    TionSensorDescription(
        key="co2",
        translation_key="co2",
        feature="co2",
        value_fn=lambda station: station.co2,
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_PPM,
        suggested_display_precision=0,
    ),
    TionSensorDescription(
        key="pm25",
        translation_key="pm25",
        feature="pm25",
        value_fn=lambda station: station.pm25,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_UG_PER_M3,
        suggested_display_precision=0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for the features each device's model has."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device_view in device_views(coordinator):
        if isinstance(device_view, Breezer):
            entities.extend(
                TionSensor(coordinator, device_view, description)
                for description in BREEZER_SENSORS
                if device_view.supports(description.feature)
            )
        else:
            entities.extend(
                TionSensor(coordinator, device_view, description)
                for description in STATION_SENSORS
                if device_view.supports(description.feature)
            )
    async_add_entities(entities)


class TionSensor[ViewT: Breezer | Station](TionEntity[ViewT], SensorEntity):
    """A sensor of a breezer or a station."""

    entity_description: TionSensorDescription[ViewT]

    def __init__(
        self,
        coordinator: TionCoordinator,
        device_view: ViewT,
        description: TionSensorDescription[ViewT],
    ) -> None:
        """Create the sensor for one device."""
        super().__init__(coordinator, device_view, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the sensor's value."""
        return self.entity_description.value_fn(self.device_view)
