"""Switches of Tion breezers and stations."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Breezer, Station
from .coordinator import TionConfigEntry, TionCoordinator
from .entity import TionEntity, TionRoomAutoEntity, device_views

# Only MagicAir stations get the room auto mode switch, as before v4.
AUTO_MODE_PRODUCTS = frozenset({"bs3xx_rf", "bs4xx_rf"})


@dataclass(frozen=True, kw_only=True)
class TionSwitchDescription[ViewT: Breezer | Station](SwitchEntityDescription):
    """A switch over one settable property of the device."""

    command: str
    exists_fn: Callable[[ViewT], bool] = lambda _: True
    available_fn: Callable[[ViewT], bool] = lambda _: True
    is_on_fn: Callable[[ViewT], bool | None]


BREEZER_SWITCHES: tuple[TionSwitchDescription[Breezer], ...] = (
    TionSwitchDescription(
        key="backlight",
        translation_key="backlight",
        command="backlight",
        is_on_fn=lambda breezer: breezer.backlight,
    ),
    TionSwitchDescription(
        key="sound",
        translation_key="sound",
        command="sound",
        is_on_fn=lambda breezer: breezer.sound,
    ),
    TionSwitchDescription(
        key="heater",
        translation_key="heater",
        command="heater_enabled",
        # Only a reported value tells whether a heater is installed.
        exists_fn=lambda breezer: breezer.heater_installed is not False,
        available_fn=lambda breezer: breezer.heater_installed is True,
        is_on_fn=lambda breezer: breezer.heater_enabled,
    ),
)

STATION_SWITCHES: tuple[TionSwitchDescription[Station], ...] = (
    TionSwitchDescription(
        key="backlight",
        translation_key="backlight",
        command="backlight",
        is_on_fn=lambda station: station.backlight,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switches each device's model can set."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    for device_view in device_views(coordinator):
        if isinstance(device_view, Breezer):
            entities.extend(
                TionSwitch(coordinator, device_view, description)
                for description in BREEZER_SWITCHES
                if device_view.can_set(description.command)
                and description.exists_fn(device_view)
            )
            continue
        entities.extend(
            TionSwitch(coordinator, device_view, description)
            for description in STATION_SWITCHES
            if device_view.can_set(description.command)
        )
        if (
            device_view.device.product_id in AUTO_MODE_PRODUCTS
            and device_view.device.room_id is not None
        ):
            entities.append(TionAutoModeSwitch(coordinator, device_view))
    async_add_entities(entities)


class TionSwitch[ViewT: Breezer | Station](TionEntity[ViewT], SwitchEntity):
    """A switch over a settable property of a breezer or a station."""

    entity_description: TionSwitchDescription[ViewT]

    def __init__(
        self,
        coordinator: TionCoordinator,
        device_view: ViewT,
        description: TionSwitchDescription[ViewT],
    ) -> None:
        """Create the switch for one device."""
        super().__init__(coordinator, device_view, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return True while the device is reachable and the switch applies."""
        return super().available and self.entity_description.available_fn(
            self.device_view
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the property is on."""
        return self.entity_description.is_on_fn(self.device_view)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the property on."""
        await self.async_send_command(**{self.entity_description.command: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the property off."""
        await self.async_send_command(**{self.entity_description.command: False})


class TionAutoModeSwitch(TionRoomAutoEntity[Station], SwitchEntity):
    """The CO2 auto mode of the room a MagicAir is in."""

    _attr_translation_key = "auto_mode"

    def __init__(self, coordinator: TionCoordinator, station: Station) -> None:
        """Create the switch for one MagicAir."""
        super().__init__(coordinator, station, "auto_mode")

    @property
    def is_on(self) -> bool | None:
        """Return True if the room's auto mode is on."""
        return auto.enabled if (auto := self.room_auto) is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the room's auto mode on."""
        await self.async_set_room_auto(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the room's auto mode off."""
        await self.async_set_room_auto(enabled=False)
