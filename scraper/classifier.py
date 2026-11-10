"""Pure response classification (status + content -> AccessClass). No I/O."""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from scraper.models import AccessClass

# ASSUMPTION: wording not yet verified against live pages. Tune after probing.
PRIVATE_MARKERS = (
    "profile is private",
    "profile is not public",
    "profile is set to private",
    "private profile",
    "not publicly available",
    "this profile isn't public",
    "this profile is not available",
)
NOT_FOUND_MARKERS = (
    "profile not found",
    "page not found",
    "could not find",
    "couldn't find",
    "doesn't exist",
    "does not exist",
)
CHALLENGE_MARKERS = (
    "captcha",
    "unusual traffic",
    "verify you are human",
    "verify you're human",
    "are you a robot",
    "automated queries",
    "attention required",
    "just a moment",
    "checking your browser",
)

_TEXT_LIMIT = 20_000


@dataclass(frozen=True, slots=True)
class Classification:
    access: AccessClass
    category: str | None
    message: str


@dataclass(frozen=True, slots=True)
class _Page:
    full: str  # visible text (error pages are small)
    head: str  # title + h1-h3 only (avoids matching profile content)
    has_markup: bool


def _parse(body: str) -> _Page:
    if not body.strip():
        return _Page("", "", False)
    soup = BeautifulSoup(body, "html.parser")
    has_markup = soup.find(True) is not None
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    parts = [soup.title.get_text(" ") if soup.title else ""]
    parts += [h.get_text(" ") for h in soup.find_all(["h1", "h2", "h3"])]
    norm = lambda s: " ".join(s.split()).lower()[:_TEXT_LIMIT]  # noqa: E731
    return _Page(norm(soup.get_text(" ")), norm(" ".join(parts)), has_markup)


def _hit(markers: tuple[str, ...], text: str) -> str | None:
    return next((m for m in markers if m in text), None)


def _c(access: AccessClass, category: str | None, message: str) -> Classification:
    return Classification(access, category, message)


def _classify_ok(content_type: str | None, body: str) -> Classification:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in ("text/html", "application/xhtml+xml"):
        return _c(AccessClass.NEEDS_REVIEW, "unexpected_content_type", f"Unexpected content type {ctype!r}.")
    if not body.strip():
        return _c(AccessClass.NEEDS_REVIEW, "empty_body", "Empty response body.")
    page = _parse(body)
    if not page.has_markup:
        return _c(AccessClass.NEEDS_REVIEW, "invalid_html", "Response body contains no HTML markup.")
    if m := _hit(CHALLENGE_MARKERS, page.head):
        return _c(AccessClass.FAILED, "blocked_by_site", f"Challenge page detected ({m!r}); stopped, no evasion.")
    private, missing = _hit(PRIVATE_MARKERS, page.head), _hit(NOT_FOUND_MARKERS, page.head)
    if private and missing:
        return _c(AccessClass.NEEDS_REVIEW, "conflicting_signals", "Page headings mix private and not-found wording.")
    if private:
        return _c(AccessClass.PRIVATE_OR_INACCESSIBLE, "private_profile_page", f"Page says profile is private ({private!r}).")
    if missing:
        return _c(AccessClass.NEEDS_REVIEW, "soft_not_found", f"HTTP 200 but headings say not found ({missing!r}).")
    return _c(AccessClass.ACCESSIBLE, None, "Profile page retrieved; contents not yet examined.")


def classify(status_code: int, content_type: str | None, body: str) -> Classification:
    if status_code == 200:
        return _classify_ok(content_type, body)
    if status_code == 429:
        return _c(AccessClass.FAILED, "rate_limited", "HTTP 429 rate limited; not retried, try again later.")
    if status_code < 200 or 200 < status_code < 400:
        return _c(AccessClass.NEEDS_REVIEW, "unexpected_status", f"Unexpected HTTP status {status_code}.")

    page = _parse(body)
    if m := _hit(CHALLENGE_MARKERS, page.full):
        return _c(AccessClass.FAILED, "blocked_by_site", f"Challenge page detected ({m!r}); stopped, no evasion.")
    if status_code >= 500:
        return _c(AccessClass.FAILED, "server_error", f"Server error {status_code}.")
    if status_code == 401:
        return _c(AccessClass.PRIVATE_OR_INACCESSIBLE, "auth_required", "HTTP 401: authentication required.")
    if status_code in (403, 404, 410):
        private, missing = _hit(PRIVATE_MARKERS, page.full), _hit(NOT_FOUND_MARKERS, page.full)
        if private and missing:
            return _c(AccessClass.NEEDS_REVIEW, "conflicting_signals", "Error page mixes private and not-found wording.")
        if private:
            return _c(AccessClass.PRIVATE_OR_INACCESSIBLE, "private_profile_page", f"Error page says profile is private ({private!r}).")
        if missing:
            if status_code in (404, 410):
                return _c(AccessClass.NOT_FOUND, "profile_not_found", f"Not-found status and page text ({missing!r}).")
            return _c(AccessClass.NEEDS_REVIEW, "not_found_text_with_403", f"HTTP 403 but page says not found ({missing!r}).")
        category = "ambiguous_access_denied" if status_code == 403 else "ambiguous_not_found"
        return _c(AccessClass.NEEDS_REVIEW, category, f"HTTP {status_code} with no explanatory wording.")
    return _c(AccessClass.NEEDS_REVIEW, "unexpected_status", f"Unexpected HTTP status {status_code}.")