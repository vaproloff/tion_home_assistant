"""PID speed calculation for Tion breezers."""

from dataclasses import dataclass


def _clamp(value: float, lower: float, upper: float) -> float:
    """Clamp a value between lower and upper bounds."""
    return max(min(value, upper), lower)


@dataclass(frozen=True, slots=True)
class PidCoefficients:
    """PID controller coefficients."""

    kp: float
    ki: float
    kd: float
    base_output: float = 0.0


@dataclass(slots=True)
class PidState:
    """PID controller state."""

    i_output: float = 0.0
    last_error: float | None = None
    last_time: float | None = None


@dataclass(frozen=True, slots=True)
class PidOutput:
    """Calculated PID output mapped to a Tion speed command."""

    error: float
    p_output: float
    i_output: float
    d_output: float
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

        p_output = self.coefficients.kp * error

        if self.coefficients.ki:
            self.state.i_output += self.coefficients.ki * error * elapsed
            self.state.i_output = _clamp(self.state.i_output, 0.0, 100.0)

        d_output = 0.0
        if elapsed > 0 and self.state.last_error is not None:
            d_output = self.coefficients.kd * (error - self.state.last_error) / elapsed

        i_output = self.state.i_output
        raw_output = self.coefficients.base_output + p_output + i_output + d_output

        self.state.last_error = error
        self.state.last_time = now

        if max_allowed == 0:
            return PidOutput(
                error=error,
                p_output=p_output,
                i_output=i_output,
                d_output=d_output,
                raw_output=raw_output,
                speed=0,
                is_on=False,
            )

        output_percent = _clamp(raw_output, 0.0, 100.0)
        speed = max(
            min(max_allowed, round(output_percent / 100 * device_max_speed)),
            min_allowed,
        )

        return PidOutput(
            error=error,
            p_output=p_output,
            i_output=i_output,
            d_output=d_output,
            raw_output=raw_output,
            speed=speed,
            is_on=speed > 0,
        )
