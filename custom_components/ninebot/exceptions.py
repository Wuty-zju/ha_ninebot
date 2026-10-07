"""Sanitized errors: upstream text can contain tokens, account or location."""

from enum import StrEnum


class ErrorKind(StrEnum):
    """Stable categories for diagnostics and translated HA errors."""

    AUTH = "auth"
    CONNECTION = "connection"
    SERVICE = "service"
    PROTOCOL = "protocol"
    PLATFORM = "platform"
    BUSY = "busy"
    CLOSED = "closed"


class NinebotError(Exception):
    """Never carry an upstream response or command line in the exception."""

    def __init__(
        self, kind: ErrorKind, *, retry_after: float | None = None, retryable: bool = False
    ) -> None:
        self.kind = kind
        self.retry_after = retry_after
        self.retryable = retryable
        self.request_revision = 0
        super().__init__(kind.value)


class NinebotAuthError(NinebotError):
    """Only explicit authentication evidence warrants reauthentication."""

    def __init__(self) -> None:
        super().__init__(ErrorKind.AUTH)
