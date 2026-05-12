"""PID speed calculation for Tion breezers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PidCoefficients:
    """PID controller coefficients."""

    kp: float
    ki: float
    kd: float


@dataclass(slots=True)
class PidState:
    """PID controller state."""

    integral: float = 0.0
    last_error: float | None = None
    last_time: float | None = None


@dataclass(frozen=True, slots=True)
class PidOutput:
    """Calculated PID output mapped to a Tion speed command."""

    error: float
    raw_output: float
    speed: int
    is_on: bool


class PidController:
    """Small PID controller that maps CO2 error to a breezer speed."""

    def __init__(self, coefficients: PidCoefficients) -> None:
        """Initialize the controller."""
        self.coefficients = coefficients
        self.state = PidState()

    def reset(self) -> None:
        """Reset accumulated PID state."""
        self.state = PidState()

    def calculate(
        self,
        *,
        source_co2: float,
        target_co2: float,
        speed_min: int,
        speed_max: int,
        device_max_speed: int,
        now: float,
    ) -> PidOutput:
        """Calculate a speed command from the current CO2 value."""
        max_allowed = max(0, min(speed_max, device_max_speed))
        min_allowed = max(0, min(speed_min, max_allowed))
        error = source_co2 - target_co2

        elapsed = 0.0
        if self.state.last_time is not None and now > self.state.last_time:
            elapsed = now - self.state.last_time

        derivative = 0.0
        if elapsed > 0 and self.state.last_error is not None:
            derivative = (error - self.state.last_error) / elapsed

        integral = 0.0
        if self.coefficients.ki != 0:
            integral = self.state.integral
            if elapsed > 0:
                integral += error * elapsed

        raw_output = self._raw_output(error, integral, derivative)
        if self.coefficients.ki != 0 and self._is_winding_up(
            raw_output, error, max_allowed
        ):
            integral = self.state.integral
            raw_output = self._raw_output(error, integral, derivative)

        self.state.integral = integral
        self.state.last_error = error
        self.state.last_time = now

        if max_allowed == 0:
            return PidOutput(
                error=error,
                raw_output=raw_output,
                speed=0,
                is_on=False,
            )

        speed = max(min(max_allowed, max(0, int(round(raw_output)))), min_allowed)

        return PidOutput(
            error=error,
            raw_output=raw_output,
            speed=speed,
            is_on=speed > 0,
        )

    def _raw_output(self, error: float, integral: float, derivative: float) -> float:
        """Return unclamped PID output."""
        return (
            self.coefficients.kp * error
            + self.coefficients.ki * integral
            + self.coefficients.kd * derivative
        )

    @staticmethod
    def _is_winding_up(raw_output: float, error: float, max_allowed: int) -> bool:
        """Return if the integral should be held at the current value."""
        return (raw_output < 0 and error < 0) or (
            raw_output > max_allowed and error > 0
        )
