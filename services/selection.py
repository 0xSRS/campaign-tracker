"""Pure eligibility rules for the three verification modes. No I/O."""

from __future__ import annotations

from collections.abc import Collection, Iterable

from core.constants import VerificationStatus as V
from services.verification_models import ExcludedRow, ExclusionReason as R, SelectionMode, StudentSnapshot

# Technical, possibly temporary. Everything else (ssl_error, parser_error, invalid_url,
# response_too_large, redirect problems, unexpected_error) is NOT retried automatically.
RETRYABLE_FAILURE_CATEGORIES = frozenset(
    {"timeout", "dns_failure", "connection_error", "network_error", "server_error", "rate_limited"}
)

_STATUS_REASON = {
    V.PRIVATE_INACCESSIBLE: (R.PRIVATE_OR_INACCESSIBLE, "Private/inaccessible; needs the student to fix access"),
    V.NOT_FOUND: (R.NOT_FOUND, "Profile not found; check the URL with the student"),
    V.NEEDS_REVIEW: (R.NEEDS_REVIEW, "Ambiguous result; review manually"),
}


def _exclude(s: StudentSnapshot, reason: R, detail: str) -> ExcludedRow:
    return ExcludedRow(s.student_id, s.url, reason, detail)


def classify(mode: SelectionMode, s: StudentSnapshot) -> ExcludedRow | None:
    """None = eligible; otherwise the exclusion row."""
    st = s.latest_status
    if mode is SelectionMode.ALL:
        return None

    if mode is SelectionMode.INCOMPLETE:
        if st is V.INCOMPLETE:
            return None
        if st is None:
            return _exclude(s, R.NEVER_VERIFIED, "Never verified")
        if st is V.COMPLETED:
            return _exclude(s, R.ALREADY_COMPLETED, "Already completed")
        return _exclude(s, R.LATEST_NOT_INCOMPLETE, f"Latest attempt was {st.value}, not a successful INCOMPLETE")

    # RETRY_FAILED
    if st is None:
        return _exclude(s, R.NEVER_VERIFIED, "Never verified")
    if st in (V.COMPLETED, V.INCOMPLETE):
        return _exclude(s, R.LATEST_EVALUATED_OK, f"Latest attempt succeeded ({st.value})")
    if st in _STATUS_REASON:
        reason, detail = _STATUS_REASON[st]
        return _exclude(s, reason, detail)
    if s.latest_error_category in RETRYABLE_FAILURE_CATEGORIES:  # st is FAILED
        return None
    return _exclude(s, R.NON_RETRYABLE_FAILURE,
                    f"Failure '{s.latest_error_category or 'unknown'}' is not automatically retryable")


def select_students(mode: SelectionMode, snapshots: Iterable[StudentSnapshot], *,
                    subset: Collection[int] | None = None) -> tuple[list[StudentSnapshot], list[ExcludedRow]]:
    eligible: list[StudentSnapshot] = []
    excluded: list[ExcludedRow] = []
    for s in snapshots:
        if subset is not None and s.student_id not in subset:
            excluded.append(_exclude(s, R.NOT_IN_SUBSET, "Not in the requested set of students"))
            continue
        row = classify(mode, s)
        (excluded if row else eligible).append(row or s)  # type: ignore[arg-type]
    return eligible, excluded