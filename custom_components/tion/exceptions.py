"""Tion integration errors."""


class TionError(Exception):
    """Base Tion client error."""


class TionAuthError(TionError):
    """Tion authentication error."""


class TionConnectionError(TionError):
    """Tion connection error."""


class TionApiError(TionError):
    """Unexpected Tion API error."""
