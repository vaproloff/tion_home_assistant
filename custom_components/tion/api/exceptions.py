"""Tion integration errors."""


class TionError(Exception):
    """Base Tion client error."""


class TionAuthError(TionError):
    """Tion authentication error."""


class TionAuthStaleError(TionAuthError):
    """Renewals have been failing for an hour: maybe an outage, not a refusal."""


class TionConnectionError(TionError):
    """Tion connection error."""


class TionDeviceTimeoutError(TionConnectionError):
    """A device did not answer a command in time."""


class TionApiError(TionError):
    """Unexpected Tion API error."""

    def __init__(self, message: str, *, grpc_status: int | None = None) -> None:
        """Keep the gRPC status the server answered with, if any."""
        super().__init__(message)
        self.grpc_status = grpc_status


class TionCommandError(TionApiError):
    """A device refused a command."""

    def __init__(self, code: int, message: str) -> None:
        """Keep the device's error code and message."""
        super().__init__(f"Command refused ({code}): {message}")
        self.code = code
        self.message = message
