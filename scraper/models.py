"""Result types for the profile fetcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.constants import VerificationStatus


class AccessClass(StrEnum):
    ACCESSIBLE = "ACCESSIBLE"
    PRIVATE_OR_INACCESSIBLE = "PRIVATE_OR_INACCESSIBLE"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"

    @property
    def verification_status(self) -> VerificationStatus | None:
        """Status to store when the profile could not be read.

        None for ACCESSIBLE: the page was fetched but courses are not evaluated yet.
        """
        return _STATUS_MAP.get(self)


_STATUS_MAP = {
    AccessClass.PRIVATE_OR_INACCESSIBLE: VerificationStatus.PRIVATE_INACCESSIBLE,
    AccessClass.NOT_FOUND: VerificationStatus.NOT_FOUND,
    AccessClass.FAILED: VerificationStatus.FAILED,
    AccessClass.NEEDS_REVIEW: VerificationStatus.NEEDS_REVIEW,
}


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    final_url: str | None
    http_status: int | None
    headers: dict[str, str]  # allow-listed, lower-case keys
    html: str | None  # only when access is ACCESSIBLE
    body_excerpt: str | None  # short text sample for non-accessible outcomes
    fetched_at: datetime
    duration_seconds: float  # total, including retries
    access: AccessClass
    error_category: str | None  # stable machine-readable code
    diagnostic: str  # human-readable evidence
    redirect_chain: tuple[str, ...] = field(default_factory=tuple)
    unfollowed_redirect: str | None = None
    attempts: int = 1

    @property
    def verification_status(self) -> VerificationStatus | None:
        return self.access.verification_status