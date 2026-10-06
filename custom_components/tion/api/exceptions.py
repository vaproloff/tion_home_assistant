"""Tion integration errors."""


class TionError(Exception):
    """Base Tion client error."""


class TionAuthError(TionError):
    """Tion authentication error."""


class TionConnectionError(TionError):
    """Tion connection error."""


class TionDeviceTimeoutError(TionConnectionError):
    """A device did not answer a command in time."""


class TionApiError(TionError):
    """Unexpected Tion API error."""


class TionCommandError(TionApiError):
    """A device refused a command."""

    def __init__(self, code: int, message: str) -> None:
        """Keep the device's error code and message."""
        super().__init__(f"Command refused ({code}): {message}")
        self.code = code
        self.message = message
