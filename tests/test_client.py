"""Tests for the Tion API client."""

from custom_components.tion.client import (
    API2_PROFILE,
    API_PROFILE,
    DEFAULT_PROFILE,
    PROFILES,
    PROFILES_BY_NAME,
    TionApiProfile,
)


def test_profiles_are_two_equivalent_endpoints() -> None:
    """Both profiles exist, are distinct, and api is the default."""
    assert PROFILES == [API_PROFILE, API2_PROFILE]
    assert PROFILES_BY_NAME == {"api": API_PROFILE, "api2": API2_PROFILE}
    assert DEFAULT_PROFILE is API_PROFILE
    assert API_PROFILE.endpoint == "https://api.magicair.tion.ru/"
    assert API2_PROFILE.endpoint == "https://api2.magicair.tion.ru/"
    assert API_PROFILE.grant_type == "extended"
    assert API2_PROFILE.grant_type == "password"
    assert API_PROFILE.scope is not None
    assert API2_PROFILE.scope is None


def test_base_headers_never_set_content_type() -> None:
    """Content-Type must be left to aiohttp so form auth is not broken."""
    for profile in PROFILES:
        assert isinstance(profile, TionApiProfile)
        assert "Content-Type" not in profile.base_headers
        assert profile.base_headers["Host"] == profile.host
