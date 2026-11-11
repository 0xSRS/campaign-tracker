"""Campaign evaluator: structured fetch/parse output + campaign config -> EvaluationResult.

Pure and deterministic: no HTTP, no HTML parsing, no UI, no database. The caller supplies
the timestamp. Module 8 stores `to_record_fields()` (one VerificationRecord row per
evaluation, append-only so history is preserved) and `result_rows()`.

Rules
-----
* Fetch not ACCESSIBLE            -> PRIVATE_INACCESSIBLE / NOT_FOUND / FAILED / NEEDS_REVIEW
  (mapped from the fetch outcome; counts are None, never 0).
* No parser output, PARSER_ERROR  -> FAILED (counts None).
* Parser UNSUPPORTED/INCOMPLETE   -> NEEDS_REVIEW (counts None).
* Parser success                  -> matched against THIS campaign's courses (the parser's own
  matches are not trusted, so a changed campaign list is always honoured):
    - template id is the stable key; exact normalised title only when the page has no template id
    - a credential whose template id is not required is never counted, even if its title matches
    - one completion per required course, however many credentials/duplicates exist
    - all required found                          -> COMPLETED
    - some missing, no ambiguity                  -> INCOMPLETE (0 of N is valid for a parsed empty profile)
    - some missing AND ambiguous/conflicting data -> NEEDS_REVIEW (a course might really be done)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.constants import VerificationStatus
from scraper.models import AccessClass
from scraper.parser.normalize import normalize_title

EVALUATOR_VERSION = "1.0.0"
_PARSER_OK = {"SUCCESS_WITH_CREDENTIALS", "SUCCESS_EMPTY"}


@dataclass(frozen=True, slots=True)
class CompletedCourse:
    template_id: int
    course_name: str
    credential_id: str | None
    match_method: str  # "template_id" | "title"
    credential_title: str
    earned_text: str | None
    evidence: str  # raw card HTML from the parser
    extra_credential_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MissingCourse:
    template_id: int
    course_name: str


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    status: VerificationStatus
    total_required: int
    evaluated_at: datetime
    completed: tuple[CompletedCourse, ...] = ()
    missing: tuple[MissingCourse, ...] = ()
    unmatched_titles: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error_category: str | None = None
    diagnostic: str | None = None
    parser_version: str | None = None
    evaluator_version: str = EVALUATOR_VERSION

    @property
    def profile_was_read(self) -> bool:
        return self.status.profile_was_read

    @property
    def completed_count(self) -> int | None:
        return len(self.completed) if self.profile_was_read else None

    @property
    def missing_count(self) -> int | None:
        return len(self.missing) if self.profile_was_read else None

    @property
    def progress_percent(self) -> float | None:
        if not self.profile_was_read:
            return None
        return round(100 * len(self.completed) / self.total_required, 1)

    def summary_lines(self) -> list[str]:
        if not self.profile_was_read:
            return [f"Total required: {self.total_required}", "Completed: n/a", "Missing: n/a",
                    "Progress: n/a", f"Status: {self.status.value}"]
        pct_s = f"{self.progress_percent:g}%"
        return [f"Total required: {self.total_required}", f"Completed: {len(self.completed)}",
                f"Missing: {len(self.missing)}", f"Progress: {pct_s}", f"Status: {self.status.value}"]

    # --- Module 8 hand-off -------------------------------------------------
    def to_record_fields(self) -> dict[str, Any]:
        """Column values for a NEW VerificationRecord row (never update an old one)."""
        return {
            "status": self.status,
            "verified_at": self.evaluated_at,
            "completed_count": self.completed_count,  # None unless the profile was read
            "missing_count": self.missing_count,
            "error_category": self.error_category,
            "diagnostic_message": self.diagnostic,
        }

    def result_rows(self) -> list[dict[str, Any]]:
        """One row per required course that was evaluated; empty when the profile was not read."""
        rows = [{"template_id": c.template_id, "course_name": c.course_name, "completed": True,
                 "credential_id": c.credential_id, "match_method": c.match_method,
                 "evidence": c.evidence} for c in self.completed]
        rows += [{"template_id": m.template_id, "course_name": m.course_name, "completed": False,
                  "credential_id": None, "match_method": None, "evidence": None} for m in self.missing]
        return rows if self.profile_was_read else []


def _not_read(campaign: Any, now: datetime, status: VerificationStatus, category: str | None,
              diagnostic: str | None, warnings: tuple[str, ...] = (),
              parser_version: str | None = None) -> EvaluationResult:
    return EvaluationResult(status=status, total_required=len(campaign.courses), evaluated_at=now,
                            warnings=warnings, error_category=category, diagnostic=diagnostic,
                            parser_version=parser_version)


def evaluate(campaign: Any, *, access: AccessClass | None, extraction: Any | None,
             evaluated_at: datetime | None = None, fetch_category: str | None = None,
             fetch_diagnostic: str | None = None) -> EvaluationResult:
    """`campaign`: object with `.courses` (name, template_id). `access`: fetcher's AccessClass.
    `extraction`: parser ExtractionResult (None if parsing did not run)."""
    now = evaluated_at or datetime.now(UTC)
    courses = list(campaign.courses)
    if not courses:
        raise ValueError("Campaign has no required courses")

    if access is None:
        return _not_read(campaign, now, VerificationStatus.FAILED, "no_fetch_result", "No fetch result supplied.")
    if access is not AccessClass.ACCESSIBLE:
        return _not_read(campaign, now, access.verification_status, fetch_category, fetch_diagnostic)
    if extraction is None:
        return _not_read(campaign, now, VerificationStatus.FAILED, "no_extraction_result",
                         "Profile was fetched but no parser output was supplied.")

    pstatus = str(getattr(extraction, "status", ""))
    pversion = getattr(extraction, "parser_version", None)
    pwarn = tuple(getattr(extraction, "warnings", ()) or ())
    if pstatus == "PARSER_ERROR":
        return _not_read(campaign, now, VerificationStatus.FAILED, "parser_error",
                         getattr(extraction, "error", None), pwarn, pversion)
    if pstatus not in _PARSER_OK or extraction.credentials is None:
        return _not_read(campaign, now, VerificationStatus.NEEDS_REVIEW, f"parser_{pstatus.lower() or 'unknown'}",
                         pwarn[0] if pwarn else "Profile structure could not be read reliably.", pwarn, pversion)

    by_tid = {c.template_id: c for c in courses}
    by_title: dict[str, list[Any]] = defaultdict(list)
    for c in courses:
        by_title[normalize_title(c.name)].append(c)

    hits: dict[int, list[tuple[Any, str]]] = defaultdict(list)
    unmatched: list[str] = []
    review: list[str] = []
    warnings: list[str] = []
    seen: set[Any] = set()
    for cred in extraction.credentials:
        key = cred.credential_id or (cred.template_id, cred.normalized_title)
        if key in seen:
            continue  # same credential repeated: no effect
        seen.add(key)
        title_hits = by_title.get(cred.normalized_title, [])
        course, method = None, "template_id"
        if cred.template_id is not None:
            course = by_tid.get(cred.template_id)
            if course is None and title_hits:
                review.append(f"conflict: {cred.title!r} has required title but template id {cred.template_id}")
        elif len(title_hits) == 1:
            course, method = title_hits[0], "title"
            warnings.append(f"matched_by_title_only: {cred.title!r}")
        elif len(title_hits) > 1:
            review.append(f"ambiguous_title: {cred.title!r} matches {len(title_hits)} required courses")
        if course is None:
            unmatched.append(cred.title)
        else:
            hits[course.template_id].append((cred, method))

    completed: list[CompletedCourse] = []
    missing: list[MissingCourse] = []
    for course in courses:  # campaign order, deterministic
        found = hits.get(course.template_id)
        if not found:
            missing.append(MissingCourse(course.template_id, course.name))
            continue
        found.sort(key=lambda h: (h[1] != "template_id", h[0].credential_id or ""))
        cred, method = found[0]
        extra = tuple(h.credential_id for h, _ in found[1:] if h.credential_id)
        if len(found) > 1:
            warnings.append(f"course_awarded_multiple_times: {course.name!r} counted once")
        completed.append(CompletedCourse(course.template_id, course.name, cred.credential_id, method,
                                         cred.title, cred.earned_text, cred.evidence, extra))

    parser_tids = {m.template_id for m in getattr(extraction, "matched", ())}
    if parser_tids != {c.template_id for c in completed}:
        warnings.append("campaign_differs_from_parser_match: result computed from current campaign")

    if not missing:
        status = VerificationStatus.COMPLETED
    elif review:
        status = VerificationStatus.NEEDS_REVIEW
    else:
        status = VerificationStatus.INCOMPLETE

    if status is VerificationStatus.NEEDS_REVIEW:
        return EvaluationResult(status=status, total_required=len(courses), evaluated_at=now,
                                warnings=tuple(review + warnings), error_category="ambiguous_match",
                                diagnostic="; ".join(review), parser_version=pversion,
                                unmatched_titles=tuple(unmatched))
    return EvaluationResult(status=status, total_required=len(courses), evaluated_at=now,
                            completed=tuple(completed), missing=tuple(missing),
                            unmatched_titles=tuple(unmatched), warnings=tuple(review + warnings),
                            parser_version=pversion)