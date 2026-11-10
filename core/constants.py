"""Application-wide constants and shared enums."""

from enum import StrEnum

APP_NAME = "Learn With Communities Campaign Tracker"
APP_VERSION = "0.1.0"
CSV_HEADER = "Google Skills Profile URL"


class VerificationStatus(StrEnum):
    """The six outcomes a profile check can have.

    The value is what we will store in the database, so keep these stable.
    """

    COMPLETED = "COMPLETED"                      # all required courses found
    INCOMPLETE = "INCOMPLETE"                    # checked OK, some courses missing
    PRIVATE_INACCESSIBLE = "PRIVATE_INACCESSIBLE"  # access is restricted
    NOT_FOUND = "NOT_FOUND"                      # genuinely non-existent profile
    FAILED = "FAILED"                            # network/server/timeout/parse failure
    NEEDS_REVIEW = "NEEDS_REVIEW"                # ambiguous, cannot classify reliably

    @property
    def label(self) -> str:
        """Human-friendly text for the UI."""
        return _STATUS_LABELS[self]

    @property
    def profile_was_read(self) -> bool:
        """True only if we actually read the profile's certifications.

        This guards the key rule: a FAILED fetch must never be treated as
        'checked successfully with zero certifications'.
        """
        return self in (VerificationStatus.COMPLETED, VerificationStatus.INCOMPLETE)


_STATUS_LABELS = {
    VerificationStatus.COMPLETED: "Completed",
    VerificationStatus.INCOMPLETE: "Incomplete",
    VerificationStatus.PRIVATE_INACCESSIBLE: "Private / Inaccessible",
    VerificationStatus.NOT_FOUND: "Not Found",
    VerificationStatus.FAILED: "Failed",
    VerificationStatus.NEEDS_REVIEW: "Needs Review",
}

class VerificationMode(StrEnum):
    MANUAL = "manual"
    BULK = "bulk"
    RETRY = "retry"


class ImportOutcome(StrEnum):
    ADDED = "added"
    EXISTING = "existing"
    CASE_CONFLICT = "case_conflict"