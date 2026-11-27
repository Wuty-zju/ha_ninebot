"""Fixed, non-sensitive explanations for local Assistant failures."""

import json
from collections.abc import Mapping
from typing import Any

from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .assistant_limits import ReadLimitExceeded
from .compat import validation as vol

TOOL_TIMEOUT = 10
MAX_TOOL_BYTES = 16 * 1024


class AssistantError(Exception):
    """Only a fixed, reviewed error code may cross the model boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


TOOL_ERRORS = (
    ReadLimitExceeded,
    Unauthorized,
    vol.Invalid,
    AssistantError,
    TimeoutError,
    HomeAssistantError,
)


ERROR_MESSAGES = {
    "task_capacity": "Too many task applications are waiting. Review them or wait for expiry.",
    "task_not_found": "Task unavailable. List tasks again; missing does not mean completed.",
    "approval_expired": "The plan expired. Preview the range and request a new plan.",
    "not_authorized": (
        "Access denied for this user or vehicle. No data was returned. "
        "Check the exact supplied vehicle reference and Home Assistant permissions. "
        "This does not establish whether a trip or vehicle exists, or which check failed."
    ),
    "invalid_arguments": (
        "Arguments do not match the tool. Use the user's selected vehicle and date range."
    ),
    "local_data_missing": (
        "The requested data is not available or verified locally. "
        "This does not prove zero trips. No cloud fetch was performed."
    ),
    "retry_later": (
        "The local read could not complete now. Respect retry_after_seconds when present. "
        "Do not loop or refresh the cloud."
    ),
    "timeout": "The local read timed out. Its result is unknown, not zero or an empty period.",
    "auth_required": (
        "The account needs reauthentication in Home Assistant. "
        "Do not request a password or token in chat."
    ),
    "unsupported_capability": "This local operation or response shape is not supported.",
    "response_too_large": "The result exceeds the output limit. Narrow the requested local page.",
}


def error_data(code: str, retry_after: int | None = None) -> dict[str, Any]:
    """Keep unknown exception text and arbitrary codes outside model output."""
    safe_code = code if code in ERROR_MESSAGES else "unsupported_capability"
    return {
        "code": safe_code,
        "message": ERROR_MESSAGES[safe_code],
        **({"retry_after_seconds": retry_after} if retry_after is not None else {}),
    }


def checked_result(response: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode()) > MAX_TOOL_BYTES:
        raise AssistantError("response_too_large")
    return response, False


def tool_failure(
    request_id: str, error: Exception | str, *, service_errors: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], bool]:
    retry_after = None
    if isinstance(error, str):
        code = error
    elif isinstance(error, ReadLimitExceeded):
        code, retry_after = "retry_later", error.retry_after
    elif isinstance(error, Unauthorized):
        code = "not_authorized"
    elif isinstance(error, vol.Invalid):
        code = "invalid_arguments"
    elif isinstance(error, AssistantError):
        code = error.code
    elif isinstance(error, TimeoutError):
        code = "timeout"
    elif isinstance(error, HomeAssistantError) and service_errors:
        code = service_errors.get(error.translation_key or "", "unsupported_capability")
    else:
        code = "unsupported_capability"
    return {
        "schema_version": 1,
        "request_id": request_id,
        "error": error_data(code, retry_after),
    }, True
