"""Value objects describing a planned local PID actuation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import TionData


@dataclass(frozen=True)
class ZoneCommand:
    """Return a zone to MANUAL — precondition for local PID actuation."""

    guid: str
    co2: int


@dataclass(frozen=True)
class BreezerCommand:
    """Desired breezer state to send to the cloud.

    Field set matches the kwargs of ``TionClient.send_breezer`` so that
    ``dataclasses.asdict(command)`` splats straight into ``async_send_breezer``.
    """

    guid: str
    is_on: bool
    speed: int
    t_set: int
    speed_min_set: int
    speed_max_set: int
    heater_enabled: Any
    heater_mode: Any
    gate: Any


@dataclass(frozen=True)
class PidIntent:
    """A planned local PID actuation for one breezer, ready to commit."""

    breezer_guid: str
    zone_command: ZoneCommand | None = None
    breezer_command: BreezerCommand | None = None

    def apply(self, data: TionData) -> None:
        """Optimistically reflect the breezer command onto the snapshot."""
        if self.breezer_command is None:
            return
        device = data.device(self.breezer_guid)
        if device is not None:
            device.data.speed = self.breezer_command.speed
            device.data.is_on = self.breezer_command.is_on
