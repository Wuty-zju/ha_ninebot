"""Fixed, non-sensitive explanations for local Assistant failures."""

from typing import Any

ERROR_MESSAGES = {
    "not_authorized": (
        "Access denied for this user or vehicle. No data was returned. "
        "This does not mean the trip is missing. Review permissions in Home Assistant."
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
