"""Tests for Tion local PID calculation."""

from custom_components.tion.pid import PidCoefficients, PidController


def test_pid_maps_positive_error_to_speed() -> None:
    """Test positive CO2 error maps percent output to device speed."""
    controller = PidController(PidCoefficients(kp=0.5, ki=0.0, kd=0.0))

    output = controller.calculate(
        source_co2=900,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )

    assert output.raw_output == 50
    assert output.speed == 3
    assert output.is_on is True


def test_pid_clamps_percent_output_to_full_speed() -> None:
    """Test output above 100 percent is clamped to full device speed."""
    controller = PidController(PidCoefficients(kp=0.5, ki=0.0, kd=0.0))

    output = controller.calculate(
        source_co2=1000,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )

    assert output.raw_output == 100
    assert output.speed == 6
    assert output.is_on is True


def test_pid_adds_base_output_at_target() -> None:
    """Test base output keeps the breezer running at the target CO2."""
    controller = PidController(
        PidCoefficients(kp=0.5, ki=0.0, kd=0.0, base_output=20.0)
    )

    output = controller.calculate(
        source_co2=800,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )

    assert output.raw_output == 20
    assert output.speed == 1
    assert output.is_on is True


def test_pid_base_output_can_be_overcome_below_target() -> None:
    """Test negative error can lower output below the base output."""
    controller = PidController(
        PidCoefficients(kp=0.5, ki=0.0, kd=0.0, base_output=20.0)
    )

    output = controller.calculate(
        source_co2=700,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )

    assert output.raw_output == -30
    assert output.speed == 0
    assert output.is_on is False


def test_pid_turns_off_when_min_speed_allows_zero() -> None:
    """Test output 0 turns the breezer off when min speed is 0."""
    controller = PidController(PidCoefficients(kp=0.5, ki=0.0, kd=0.0))

    output = controller.calculate(
        source_co2=700,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )

    assert output.raw_output == -50
    assert output.speed == 0
    assert output.is_on is False


def test_pid_respects_non_zero_min_speed() -> None:
    """Test output below a non-zero minimum speed is raised to the minimum."""
    controller = PidController(PidCoefficients(kp=0.5, ki=0.0, kd=0.0))

    output = controller.calculate(
        source_co2=700,
        target_co2=800,
        speed_min=2,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )

    assert output.speed == 2
    assert output.is_on is True


def test_pid_clamps_to_max_speed() -> None:
    """Test percent output is clamped by configured max speed."""
    controller = PidController(PidCoefficients(kp=0.5, ki=0.0, kd=0.0))

    output = controller.calculate(
        source_co2=1000,
        target_co2=800,
        speed_min=0,
        speed_max=4,
        device_max_speed=6,
        now=0,
    )

    assert output.speed == 4
    assert output.is_on is True


def test_pid_reset_clears_i_output() -> None:
    """Test reset clears accumulated integral output state."""
    controller = PidController(PidCoefficients(kp=0.0, ki=0.01, kd=0.0))

    controller.calculate(
        source_co2=810,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )
    controller.calculate(
        source_co2=810,
        target_co2=800,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=10,
    )

    assert controller.state.i_output == 1.0

    controller.reset()

    assert controller.state.i_output == 0
    assert controller.state.last_error is None
    assert controller.state.last_time is None


def test_pid_anti_windup_clamps_integral_output_at_upper_limit() -> None:
    """Test integral output is clamped at 100 percent."""
    controller = PidController(PidCoefficients(kp=0.0, ki=0.01, kd=0.0))

    controller.calculate(
        source_co2=1800,
        target_co2=800,
        speed_min=0,
        speed_max=4,
        device_max_speed=6,
        now=0,
    )
    controller.calculate(
        source_co2=1800,
        target_co2=800,
        speed_min=0,
        speed_max=4,
        device_max_speed=6,
        now=20,
    )
    output = controller.calculate(
        source_co2=1800,
        target_co2=800,
        speed_min=0,
        speed_max=4,
        device_max_speed=6,
        now=40,
    )

    assert output.raw_output == 100
    assert output.speed == 4
    assert controller.state.i_output == 100


def test_pid_anti_windup_clamps_integral_output_at_lower_limit() -> None:
    """Test integral output is clamped at -100 percent."""
    controller = PidController(PidCoefficients(kp=0.0, ki=0.01, kd=0.0))

    controller.calculate(
        source_co2=0,
        target_co2=1000,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=0,
    )
    controller.calculate(
        source_co2=0,
        target_co2=1000,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=20,
    )
    output = controller.calculate(
        source_co2=0,
        target_co2=1000,
        speed_min=0,
        speed_max=6,
        device_max_speed=6,
        now=40,
    )

    assert output.raw_output == -100
    assert output.speed == 0
    assert controller.state.i_output == -100
