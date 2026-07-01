"""Tests for Tion diagnostic sensors."""

from types import SimpleNamespace

from custom_components.tion.sensor import TionApiProfileSensor
from homeassistant.const import EntityCategory


def _coordinator(
    active_profile: str = "api", *, last_update_success: bool = True
) -> SimpleNamespace:
    """Return a coordinator double exposing the client's active profile."""
    return SimpleNamespace(
        client=SimpleNamespace(active_profile=active_profile),
        last_update_success=last_update_success,
    )


def test_api_profile_sensor_reports_active_profile() -> None:
    """Test the sensor value is the client's active API profile."""
    sensor = TionApiProfileSensor(_coordinator("api2"), "entry-1")

    assert sensor.native_value == "api2"


def test_api_profile_sensor_unique_id_uses_entry() -> None:
    """Test the unique id is derived from the config entry id."""
    sensor = TionApiProfileSensor(_coordinator(), "entry-xyz")

    assert sensor.unique_id == "entry-xyz_api_profile"


def test_api_profile_sensor_is_diagnostic_and_disabled_by_default() -> None:
    """Test the sensor is a diagnostic entity disabled by default."""
    sensor = TionApiProfileSensor(_coordinator(), "entry-1")

    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False


def test_api_profile_sensor_available_follows_coordinator() -> None:
    """Test availability follows the coordinator, not the breezers' reachability."""
    available = TionApiProfileSensor(_coordinator(last_update_success=True), "e")
    unavailable = TionApiProfileSensor(_coordinator(last_update_success=False), "e")

    assert available.available is True
    assert unavailable.available is False
