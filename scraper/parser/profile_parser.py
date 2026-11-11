"""Parse a Google Skills public profile page (skills.google/public_profiles/<uuid>).

Observed structure (verified against a saved real page):
  div.profile-badges > div.profile-badge (one per awarded badge)
    a.badge-image[href=".../public_profiles/<uuid>/badges/<AWARDED_ID>"]
    span.ql-title-medium            -> badge title
    span.ql-body-medium             -> "Earned <date>"
    ql-button[modal="public-profile-award-modal-N"]
  ql-dialog#public-profile-award-modal-N (sibling of main, same static HTML)
    ql-button[href="/course_templates/<TEMPLATE_ID>"]

The awarded id (badges/<id>) is NOT the template id. The template id is only
reachable via the card's modal dialog, so a card links to its dialog by id.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from scraper.parser.models import (
    ExtractedCredential,
    ExtractionResult,
    ExtractionStatus,
    MatchedCourse,
)
from scraper.parser.normalize import collapse_ws, normalize_title

logger = logging.getLogger(__name__)

PARSER_VERSION = "1.0.0"

_BADGE_ID_RE = re.compile(r"/badges/(\d+)(?:[/?#]|$)")
_TEMPLATE_RE = re.compile(r"/course_templates/(\d+)(?:[/?#]|$)")
_EVIDENCE_LIMIT = 4000


def parse_profile(html: str | None, campaign: Any) -> ExtractionResult:
    """Never raises. `campaign` needs `.courses` items with `.name` and `.template_id`."""
    try:
        return _parse(html, campaign)
    except Exception as exc:  # noqa: BLE001 - converted to an explicit failure state
        logger.exception("Profile parser failure")
        return ExtractionResult(
            status=ExtractionStatus.PARSER_ERROR, parser_version=PARSER_VERSION,
            credentials=None, error=f"{type(exc).__name__}: {exc}",
        )


def _fail(status: ExtractionStatus, msg: str, diag: dict[str, object],
          partial: tuple[ExtractedCredential, ...] = (),
          warnings: tuple[str, ...] = ()) -> ExtractionResult:
    return ExtractionResult(status=status, parser_version=PARSER_VERSION, credentials=None,
                            warnings=(msg, *warnings), diagnostics=diag, partial_credentials=partial)


def _parse(html: str | None, campaign: Any) -> ExtractionResult:
    if not isinstance(html, str) or not html.strip():
        return _fail(ExtractionStatus.INCOMPLETE_OR_UNEXPECTED, "No HTML provided.", {"html_length": 0})

    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.profile-badges")
    heading = soup.select_one("main h1")
    cards = container.select("div.profile-badge") if container else []
    diag: dict[str, object] = {
        "html_length": len(html), "has_heading": heading is not None,
        "has_badges_container": container is not None, "card_count": len(cards),
        "script_count": len(soup.find_all("script")),
    }

    if container is None:
        if heading is None:
            hint = " Page has scripts and no profile markup; it may be JavaScript-rendered." if diag["script_count"] else ""
            return _fail(ExtractionStatus.UNSUPPORTED_STRUCTURE,
                         f"No profile heading or badge section found.{hint}", diag)
        # Profile header present but no badge section. We have no verified example of an
        # empty profile, so we refuse to call this "no credentials".
        # Recognised profile header, no badge section. Treat as genuinely empty ONLY if the
        # page is complete and nothing anywhere looks like a badge (guards against renamed markup).
        complete = "</main>" in html and "</html>" in html.lower()
        badge_hints = bool(soup.select('a[href*="/badges/"], [href*="/course_templates/"], [modal]')
                           or "Earned " in soup.get_text())
        diag.update(page_complete=complete, badge_hints=badge_hints)
        if complete and not badge_hints:
            return ExtractionResult(
                status=ExtractionStatus.SUCCESS_EMPTY, parser_version=PARSER_VERSION, credentials=(),
                missing_template_ids=tuple(c.template_id for c in campaign.courses), diagnostics=diag,
                warnings=("badge_section_absent_assumed_empty: complete profile page with no badge markup",))
        return _fail(ExtractionStatus.INCOMPLETE_OR_UNEXPECTED,
                     "Profile heading found but badge section missing and page cannot be confirmed empty "
                     "(truncated page or badge-like markup present).", diag)

    if not cards:
        if container.find(True) is None and not container.get_text(strip=True):
            return ExtractionResult(
                status=ExtractionStatus.SUCCESS_EMPTY, parser_version=PARSER_VERSION, credentials=(),
                missing_template_ids=tuple(c.template_id for c in campaign.courses), diagnostics=diag)
        return _fail(ExtractionStatus.UNSUPPORTED_STRUCTURE,
                     "Badge section has content but no recognised badge cards (markup changed?).", diag)

    creds: list[ExtractedCredential] = []
    card_failures: list[str] = []
    for i, card in enumerate(cards):
        cred, problem = _parse_card(card, soup)
        if cred is None:
            card_failures.append(f"card {i}: {problem}")
        else:
            creds.append(cred)

    if not creds:
        return _fail(ExtractionStatus.UNSUPPORTED_STRUCTURE,
                     "Badge cards found but none had a readable title.", {**diag, "card_failures": card_failures})
    if card_failures:
        return _fail(ExtractionStatus.INCOMPLETE_OR_UNEXPECTED,
                     f"{len(card_failures)} of {len(cards)} badge cards unreadable.",
                     {**diag, "card_failures": card_failures}, partial=tuple(creds))

    return _build_result(creds, campaign, diag)


def _text(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    t = collapse_ws(tag.get_text(" "))
    return t or None


def _parse_card(card: Tag, soup: BeautifulSoup) -> tuple[ExtractedCredential | None, str]:
    title = _text(card.select_one("span.ql-title-medium"))
    if not title:
        return None, "missing title element"
    warnings: list[str] = []

    anchor = card.select_one("a.badge-image[href]")
    href = str(anchor["href"]) if anchor else None
    m = _BADGE_ID_RE.search(href) if href else None
    credential_id = m.group(1) if m else None
    if credential_id is None:
        warnings.append("credential_id_missing")

    template_id: int | None = None
    modal_btn = card.find(attrs={"modal": True})
    modal_id = str(modal_btn["modal"]) if modal_btn else None
    if not modal_id:
        warnings.append("detail_dialog_link_missing")
    else:
        dialog = soup.find(id=modal_id)
        if dialog is None:
            warnings.append("detail_dialog_not_in_html")
        else:
            links = {int(x.group(1)) for a in dialog.find_all(href=True)
                     if (x := _TEMPLATE_RE.search(str(a["href"])))}
            if len(links) == 1:
                template_id = links.pop()
            elif links:
                warnings.append("multiple_template_ids_in_dialog")
            else:
                warnings.append("template_id_missing_in_dialog")
            headline = dialog.get("headline")
            if headline and normalize_title(str(headline)) != normalize_title(title):
                warnings.append("dialog_title_differs_from_card_title")

    return ExtractedCredential(
        title=title, normalized_title=normalize_title(title), credential_id=credential_id,
        badge_url=href, earned_text=_text(card.select_one("span.ql-body-medium")),
        template_id=template_id, evidence=str(card)[:_EVIDENCE_LIMIT], warnings=tuple(warnings),
    ), ""


def _build_result(creds: list[ExtractedCredential], campaign: Any, diag: dict[str, object]) -> ExtractionResult:
    warnings: list[str] = []

    # De-duplicate: same awarded id repeated is one credential. No id -> same (template, title).
    seen: set[tuple] = set()
    unique: list[ExtractedCredential] = []
    for c in creds:
        key = ("id", c.credential_id) if c.credential_id else ("t", c.template_id, c.normalized_title)
        if key in seen:
            warnings.append(f"duplicate_credential_ignored: {c.title!r} ({c.credential_id or 'no id'})")
            continue
        seen.add(key)
        unique.append(c)

    by_tid = {c.template_id: c for c in campaign.courses}
    by_title: dict[str, list[Any]] = {}
    for course in campaign.courses:
        by_title.setdefault(normalize_title(course.name), []).append(course)

    matches: dict[int, list[tuple[ExtractedCredential, str]]] = {}
    unmatched: list[ExtractedCredential] = []
    for c in unique:
        course, method = None, ""
        title_hits = by_title.get(c.normalized_title, [])
        if c.template_id is not None:
            course = by_tid.get(c.template_id)
            method = "template_id"
            if course is None and title_hits:
                warnings.append(f"conflict: {c.title!r} title matches required course but template id "
                                f"{c.template_id} differs; not counted")
            elif course is not None and normalize_title(course.name) != c.normalized_title:
                warnings.append(f"title_mismatch: {c.title!r} vs configured {course.name!r}; matched by template id")
        elif len(title_hits) == 1:
            course, method = title_hits[0], "title"
            warnings.append(f"matched_by_title_only: {c.title!r} (no template id in page)")
        elif len(title_hits) > 1:
            warnings.append(f"ambiguous_title: {c.title!r} matches {len(title_hits)} required courses; not counted")
        if course is None:
            unmatched.append(c)
        else:
            matches.setdefault(course.template_id, []).append((c, method))

    matched: list[MatchedCourse] = []
    for course in campaign.courses:
        hits = matches.get(course.template_id)
        if not hits:
            continue
        first, method = hits[0]
        extra = tuple(h.credential_id for h, _ in hits[1:] if h.credential_id)
        if len(hits) > 1:
            warnings.append(f"course_awarded_multiple_times: {course.name!r} counted once")
        matched.append(MatchedCourse(course.template_id, course.name, first.credential_id,
                                     method, first, extra))

    done = {m.template_id for m in matched}
    return ExtractionResult(
        status=ExtractionStatus.SUCCESS_WITH_CREDENTIALS, parser_version=PARSER_VERSION,
        credentials=tuple(unique), matched=tuple(matched), unmatched=tuple(unmatched),
        missing_template_ids=tuple(c.template_id for c in campaign.courses if c.template_id not in done),
        warnings=tuple(warnings), diagnostics={**diag, "unique_credentials": len(unique)},
    )