"""Tests for the Tion integration's `__init__` merge logic."""

from typing import Any

import pytest

from custom_components.tion import _merge_auth_token


@pytest.mark.parametrize(
    ("stored", "profile_name", "token", "expected"),
    [
        pytest.param(
            "legacy-string-token",
            "api",
            "new-token",
            {"api": "new-token"},
            id="legacy_string_stored_is_discarded_without_crash",
        ),
        pytest.param(
            None,
            "api",
            "new-token",
            {"api": "new-token"},
            id="none_stored_creates_new_dict",
        ),
        pytest.param(
            {"api": "old-token", "api2": "other-token"},
            "api",
            "new-token",
            {"api": "new-token", "api2": "other-token"},
            id="existing_dict_merges_and_preserves_other_keys",
        ),
    ],
)
def test_merge_auth_token(
    stored: Any, profile_name: str, token: str, expected: dict[str, str | None]
) -> None:
    """Merging must coerce non-dict stored values and preserve existing keys."""
    assert _merge_auth_token(stored, profile_name, token) == expected
