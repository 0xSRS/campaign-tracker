"""Pure display helpers (no widgets, no services)."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text

from core.constants import VerificationStatus as V

_STATUS_STYLE = {
    V.COMPLETED: "bold green", V.INCOMPLETE: "yellow", V.FAILED: "bold red",
    V.PRIVATE_INACCESSIBLE: "dark_orange", V.NOT_FOUND: "magenta", V.NEEDS_REVIEW: "cyan",
}

_CATEGORY_TEXT = {
    "timeout": "Request timed out",
    "dns_failure": "DNS lookup failed",
    "connection_error": "Network connection problem",
    "network_error": "Network connection problem",
    "server_error": "Server error (HTTP 5xx)",
    "rate_limited": "Rate limited by the server (HTTP 429)",
    "ssl_error": "TLS/SSL error",
    "parser_error": "Profile page could not be parsed (layout may have changed)",
    "invalid_url": "Invalid profile URL",
    "response_too_large": "Response too large",
    "redirected_to_login": "Profile is private or requires sign-in",
    "not_found": "Profile not found (HTTP 404)",
    "unexpected_error": "Unexpected error",
    "manual_private": "Marked private manually",
}


def status_text(status: V | None) -> Text:
    if status is None:
        return Text("Not verified", style="dim")
    return Text(status.label, style=_STATUS_STYLE[status])


def describe_category(category: str | None) -> str:
    if not category:
        return "—"
    return _CATEGORY_TEXT.get(category, category.replace("_", " ").capitalize())


def fmt_dt(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "—"


def fmt_pct(pct: float | None) -> str:
    return "—" if pct is None else f"{pct:.1f}%"


def bar(pct: float | None, width: int = 16) -> str:
    filled = round(width * (pct or 0) / 100)
    return "█" * filled + "░" * (width - filled)