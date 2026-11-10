from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.constants import VerificationMode, VerificationStatus


@dataclass(frozen=True, slots=True)
class ProfileInput:
    original_url: str
    normalized_url: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class ImportCounts:
    """Same field names as ImportSummary; Module 8 can do ImportCounts(**summary.model_dump())."""

    total_rows: int = 0
    valid_urls: int = 0
    new_profiles: int = 0
    existing_profiles: int = 0
    duplicate_rows: int = 0
    blank_rows: int = 0
    invalid_urls: int = 0
    import_errors: int = 0
    needs_review: int = 0


@dataclass(frozen=True, slots=True)
class ImportStoreResult:
    batch_id: int
    added: int
    existing: int
    held_for_review: int
    linked_to_campaign: int


@dataclass(frozen=True, slots=True)
class CertificationOutcome:
    required_certification_id: int
    is_completed: bool
    matched_credential_id: str | None = None
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VerificationInput:
    student_id: int
    campaign_id: int
    status: VerificationStatus
    mode: VerificationMode = VerificationMode.MANUAL
    http_status: int | None = None
    error_category: str | None = None
    diagnostic_message: str | None = None
    duration_seconds: float | None = None
    outcomes: Sequence[CertificationOutcome] = ()