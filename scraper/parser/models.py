"""Result types for the profile parser. Pure data, no I/O, no UI imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExtractionStatus(StrEnum):
    SUCCESS_WITH_CREDENTIALS = "SUCCESS_WITH_CREDENTIALS"  # parsed, >=1 credential
    SUCCESS_EMPTY = "SUCCESS_EMPTY"  # parsed, badge section present and genuinely empty
    UNSUPPORTED_STRUCTURE = "UNSUPPORTED_STRUCTURE"  # HTML retrieved, not a profile layout we know
    INCOMPLETE_OR_UNEXPECTED = "INCOMPLETE_OR_UNEXPECTED"  # recognised page, parts missing/odd
    PARSER_ERROR = "PARSER_ERROR"  # our bug / unparseable input


SUCCESS_STATUSES = frozenset({ExtractionStatus.SUCCESS_WITH_CREDENTIALS, ExtractionStatus.SUCCESS_EMPTY})


@dataclass(frozen=True, slots=True)
class ExtractedCredential:
    title: str  # as displayed, whitespace-collapsed
    normalized_title: str
    credential_id: str | None  # awarded badge id from /badges/<id>; never invented
    badge_url: str | None
    earned_text: str | None  # raw "Earned Nov  3, 2026 EST" (not parsed into a date)
    template_id: int | None  # from /course_templates/<id> in the card's detail dialog
    evidence: str  # raw HTML of the badge card, for debugging
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MatchedCourse:
    template_id: int
    course_name: str
    credential_id: str | None
    match_method: str  # "template_id" | "title"
    credential: ExtractedCredential
    extra_credential_ids: tuple[str, ...] = ()  # same course awarded more than once


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    status: ExtractionStatus
    parser_version: str
    # None unless status is a success: None means "unknown", () means "genuinely none".
    credentials: tuple[ExtractedCredential, ...] | None
    matched: tuple[MatchedCourse, ...] = ()
    unmatched: tuple[ExtractedCredential, ...] = ()
    missing_template_ids: tuple[int, ...] = ()  # required courses not matched
    warnings: tuple[str, ...] = ()
    diagnostics: dict[str, object] = field(default_factory=dict)
    # Cards recovered from an INCOMPLETE page; for debugging only, never for counting.
    partial_credentials: tuple[ExtractedCredential, ...] = ()
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status in SUCCESS_STATUSES

    @property
    def completed_template_ids(self) -> frozenset[int]:
        return frozenset(m.template_id for m in self.matched)