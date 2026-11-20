"""Sanitized errors: upstream text can contain tokens, account or location."""

from dataclasses import dataclass, replace
from enum import StrEnum


def upstream_result_code(value: object) -> int | None:
    """A bounded numeric result code, never an arbitrary proxy string."""
    if isinstance(value, str) and 1 <= len(value) <= 8:
        digits = value.removeprefix("-")
        value = int(value) if digits and digits.isascii() and digits.isdecimal() else None
    return value if type(value) is int and -999_999 <= value <= 9_999_999 else None


class BackendError(StrEnum):
    """Observed native error formats; these labels grant no permission."""

    RESPONSE_DECRYPTION = "response_decryption"
    CONTROL_COMMAND_BUILD = "control_command_build"


def backend_error(message: object) -> BackendError | None:
    if not isinstance(message, str):
        return None
    if message.startswith(("control: decrypt failed:", "travel: decrypt failed:")):
        return BackendError.RESPONSE_DECRYPTION
    if message.startswith("control: build cmd:"):
        return BackendError.CONTROL_COMMAND_BUILD
    return None


class ErrorKind(StrEnum):
    """Stable categories for diagnostics and translated HA errors."""

    AUTH = "auth"
    CONNECTION = "connection"
    SERVICE = "service"
    PROTOCOL = "protocol"
    PLATFORM = "platform"
    BUSY = "busy"
    CLOSED = "closed"


class FailurePhase(StrEnum):
    """Observed local stages, without inferring the child's cloud protocol."""

    UNKNOWN = "unknown"
    BACKEND_START = "backend_start"
    LOOPBACK_AUTH = "loopback_auth"
    SESSION_PREPARE = "session_prepare"
    AUTH_LOGIN = "auth_login"
    ACCOUNT_IDENTITY = "account_identity"
    VEHICLES = "vehicles"
    SESSION_COMMIT = "session_commit"
    NATIVE_CONTROL = "native_control"
    MONTH_RECONCILIATION = "month_reconciliation"


class Endpoint(StrEnum):
    """Templates only; vehicle IDs, query strings and paths never enter logs."""

    UNKNOWN = "unknown"
    AUTH_LOGIN = "auth_login"
    AUTH_PROBE = "loopback_auth_probe"
    ACCOUNT = "account"
    VEHICLES = "vehicles"
    STATUS = "vehicle_status"
    BATTERY = "vehicle_battery"
    TRAVEL = "travel"
    DETAIL = "ride_detail"
    CONTROL = "control"
    REFRESH = "auth_refresh"


class EnvelopeCode(StrEnum):
    """Reviewed proxy codes; arbitrary upstream strings are not diagnostic data."""

    UNAUTHORIZED = "unauthorized"
    INVALID_AUTH = "invalid_auth"
    TOKEN_EXPIRED = "token_expired"
    UPSTREAM_ERROR = "upstream_error"
    BAD_REQUEST = "bad_request"
    OTHER = "other"


@dataclass(frozen=True)
class FailureContext:
    """Small, safe error metadata, independent of a ConfigEntry or raw payload."""

    phase: FailurePhase = FailurePhase.UNKNOWN
    endpoint: Endpoint = Endpoint.UNKNOWN
    proxy_http_status: int | None = None
    envelope_code: EnvelopeCode | None = None
    upstream_code: int | None = None
    backend_error: BackendError | None = None

    def diagnostics(self) -> dict[str, str | int | None]:
        """Only reviewed enum values and the observed local HTTP status."""
        return {
            "phase": self.phase.value,
            "endpoint": self.endpoint.value,
            "proxy_http_status": self.proxy_http_status,
            "envelope_code": self.envelope_code.value if self.envelope_code else None,
            "upstream_code": self.upstream_code,
            "backend_error": self.backend_error.value if self.backend_error else None,
        }


class NinebotError(Exception):
    """Never carry an upstream response or command line in the exception."""

    def __init__(
        self, kind: ErrorKind, *, retry_after: float | None = None, retryable: bool = False
    ) -> None:
        self.kind = kind
        self.retry_after = retry_after
        self.retryable = retryable
        self.request_revision = 0
        self.context = FailureContext()
        super().__init__(kind.value)

    def at_phase(self, phase: FailurePhase) -> None:
        """The earliest observed failure stage wins over a caller's wider scope."""
        if self.context.phase is FailurePhase.UNKNOWN:
            self.context = replace(self.context, phase=phase)

    def at_endpoint(self, endpoint: Endpoint) -> None:
        if self.context.endpoint is Endpoint.UNKNOWN:
            self.context = replace(self.context, endpoint=endpoint)

    def observe_response(self, status: int | None, raw: object = None) -> None:
        """Whitelist metadata; never retain or stringify the response itself."""
        code = None
        upstream = None
        category = None
        if isinstance(raw, dict) and isinstance(error := raw.get("error"), dict):
            value = error.get("code")
            if value is not None:
                code = (
                    EnvelopeCode(value)
                    if isinstance(value, str) and value in EnvelopeCode
                    else EnvelopeCode.OTHER
                )
            upstream = upstream_result_code(error.get("upstream_code"))
            category = backend_error(error.get("message"))
        self.context = replace(
            self.context,
            proxy_http_status=status if type(status) is int and 100 <= status <= 599 else None,
            envelope_code=code if raw is not None else self.context.envelope_code,
            upstream_code=upstream if raw is not None else self.context.upstream_code,
            backend_error=category if raw is not None else self.context.backend_error,
        )


class NinebotAuthError(NinebotError):
    """Only explicit authentication evidence warrants reauthentication."""

    def __init__(self) -> None:
        super().__init__(ErrorKind.AUTH)
