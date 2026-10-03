"""Only reviewed public vehicle assets become Home Assistant image URLs."""

import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

IMAGE_HOST = "oms-oss-public.ninebot.com"


def public_image_url(value: object) -> str | None:
    """Discard the observed opaque signature; do not guess other query semantics.

    An isolated unsigned HEAD confirmed public access to a recorded model asset.
    This is not a guarantee for every future model. Unknown origins/queries stay
    unavailable instead of passing credentials into HA's downloader or logs.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or parts.hostname != IMAGE_HOST
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or parts.fragment
        ):
            return None
        if parts.query:
            query = parse_qsl(
                parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2
            )
            if len(query) != 1 or query[0][0] != "nbchecksignv1":
                return None
        path = unquote(parts.path, errors="strict")
    except (ValueError, UnicodeError):
        return None
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(char) <= 32 or ord(char) == 127 for char in path)
        or any(part in (".", "..") for part in path.split("/"))
        or re.search(r"%(?![0-9a-fA-F]{2})", parts.path)
        or PurePosixPath(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    ):
        return None
    return urlunsplit(("https", IMAGE_HOST, parts.path, "", ""))
