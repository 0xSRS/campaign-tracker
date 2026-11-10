"""Pure URL validation and normalization. No network access."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.exceptions import InvalidProfileURLError

ALLOWED_HOSTS = frozenset({"skills.google", "www.skills.google"})
CANONICAL_HOST = "www.skills.google"
PROFILE_PATH_PREFIX = "/public_profiles/"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]*")


@dataclass(frozen=True)
class ParsedProfileURL:
    original: str      # as submitted (outer whitespace stripped only)
    normalized: str    # used for deduplication / identity
    profile_id: str    # case preserved


def parse_profile_url(raw: str) -> ParsedProfileURL:
    """Validate and normalize a profile URL, or raise InvalidProfileURLError."""
    original = raw.strip()
    if not original:
        raise InvalidProfileURLError("URL is empty")
    if not original.isascii():
        raise InvalidProfileURLError("URL contains non-ASCII characters")
    if any(ord(c) <= 32 or ord(c) == 127 for c in original):
        raise InvalidProfileURLError("URL contains whitespace or control characters")

    try:
        parts = urlsplit(original)
    except ValueError as exc:
        raise InvalidProfileURLError(f"Malformed URL: {exc}") from exc

    if parts.scheme.lower() != "https":
        raise InvalidProfileURLError("URL must use https")
    if parts.netloc.lower() not in ALLOWED_HOSTS:
        raise InvalidProfileURLError("Unsupported domain (expected skills.google)")

    path = parts.path[:-1] if parts.path.endswith("/") else parts.path
    if not path.startswith(PROFILE_PATH_PREFIX):
        raise InvalidProfileURLError("Unsupported path (expected /public_profiles/<id>)")

    profile_id = path[len(PROFILE_PATH_PREFIX):]
    if not profile_id:
        raise InvalidProfileURLError("Profile identifier is empty")
    if "/" in profile_id:
        raise InvalidProfileURLError("Unexpected extra path components")
    if not _ID_RE.fullmatch(profile_id):
        raise InvalidProfileURLError("Profile identifier has unsupported characters")

    normalized = f"https://{CANONICAL_HOST}{PROFILE_PATH_PREFIX}{profile_id}"
    return ParsedProfileURL(original, normalized, profile_id)