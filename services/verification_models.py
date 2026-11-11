"""Plain data types shared by the verification service. No Textual, DB or HTTP imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.constants import VerificationStatus


class SelectionMode(StrEnum):
    ALL = "all"  # Mode A: every registered student
    INCOMPLETE = "incomplete"  # Mode B: latest attempt is a successful INCOMPLETE evaluation
    RETRY_FAILED = "retry_failed"  # Mode C: latest attempt failed for a retryable technical reason


class ExclusionReason(StrEnum):
    NEVER_VERIFIED = "never_verified"
    ALREADY_COMPLETED = "already_completed"
    LATEST_NOT_INCOMPLETE = "latest_not_incomplete"  # failed / inaccessible / review: not Mode B's job
    LATEST_EVALUATED_OK = "latest_evaluated_ok"  # nothing failed: not Mode C's job
    PRIVATE_OR_INACCESSIBLE = "private_or_inaccessible"
    NOT_FOUND = "not_found"
    NEEDS_REVIEW = "needs_review"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    NOT_IN_SUBSET = "not_in_subset"


class ConfirmationRequired(Exception):
    """run() was called without the user's explicit confirmation of the preview."""


class RunInProgressError(Exception):
    """Only one verification run may be active at a time."""


@dataclass(frozen=True, slots=True)
class StudentSnapshot:
    student_id: int
    url: str
    latest_status: VerificationStatus | None  # latest ATTEMPT (None = never verified)
    latest_error_category: str | None = None


@dataclass(frozen=True, slots=True)
class ExcludedRow:
    student_id: int
    url: str
    reason: ExclusionReason
    detail: str


@dataclass(frozen=True, slots=True)
class VerificationPreview:
    mode: SelectionMode
    campaign_id: int
    total_registered: int
    eligible: tuple[StudentSnapshot, ...]
    excluded: tuple[ExcludedRow, ...]
    created_at: datetime

    @property
    def eligible_count(self) -> int:
        return len(self.eligible)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    @property
    def exclusion_counts(self) -> dict[ExclusionReason, int]:
        counts: dict[ExclusionReason, int] = {}
        for row in self.excluded:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return counts


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    mode: SelectionMode
    state: str  # idle | running | finished | cancelled | stopped_rate_limited | error
    total_selected: int
    processed: int
    completed: int
    incomplete: int
    failed: int
    inaccessible: int
    not_found: int
    needs_review: int
    remaining: int
    error: str | None = None
    last_profile: str | None = None  # most recently processed profile URL


@dataclass(frozen=True, slots=True)
class RunSummary:
    mode: SelectionMode
    progress: ProgressSnapshot
    record_ids: tuple[int, ...] = field(default_factory=tuple)
    not_started: int = 0
    cancelled: int = 0