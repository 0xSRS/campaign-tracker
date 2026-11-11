"""Turn one finished fetch into a VerificationInput (parse -> evaluate -> storable record).

Failures are stored as failures: a fetch/parse problem can never become INCOMPLETE here,
because the evaluator returns counts only for COMPLETED/INCOMPLETE and DB rules enforce it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from core.constants import VerificationMode, VerificationStatus
from core.exceptions import InvalidRecordError
from database.records import CertificationOutcome, VerificationInput
from scraper.batch import BatchOutcome, BatchState
from scraper.models import AccessClass

logger = logging.getLogger(__name__)
_DIAG_LIMIT = 1500


def _failed(student_id: int, campaign_id: int, mode: VerificationMode, category: str, diagnostic: str,
            http_status: int | None = None, duration: float | None = None) -> VerificationInput:
    return VerificationInput(student_id=student_id, campaign_id=campaign_id, status=VerificationStatus.FAILED,
                             mode=mode, http_status=http_status, error_category=category,
                             diagnostic_message=diagnostic[:_DIAG_LIMIT], duration_seconds=duration)


def build_verification_input(*, student_id: int, campaign_id: int, mode: VerificationMode,
                             outcome: BatchOutcome, campaign: Any, cert_ids: Mapping[int, int],
                             parser: Callable[..., Any], evaluator: Callable[..., Any],
                             now: datetime) -> VerificationInput:
    """`cert_ids`: required course template_id -> RequiredCertification.id (active ones)."""
    if outcome.state is BatchState.INVALID_URL:
        return _failed(student_id, campaign_id, mode, "invalid_url", outcome.detail or "Invalid profile URL")
    if outcome.state is not BatchState.COMPLETED or outcome.fetch is None or outcome.fetch.result is None:
        raise InvalidRecordError(f"Outcome {outcome.state.value} must not be stored as a verification.")

    fr = outcome.fetch.result
    prefix = f"attempts={outcome.fetch.attempts}; " if outcome.fetch.attempts > 1 else ""
    try:
        extraction = parser(fr.html, campaign) if fr.access is AccessClass.ACCESSIBLE else None
        ev = evaluator(campaign, access=fr.access, extraction=extraction, evaluated_at=now,
                       fetch_category=fr.error_category, fetch_diagnostic=fr.diagnostic)
    except Exception as exc:  # noqa: BLE001 - stored as FAILED, never as an empty evaluation
        logger.exception("Parse/evaluate crashed")
        return _failed(student_id, campaign_id, mode, "evaluation_error",
                       f"{prefix}{type(exc).__name__}: {exc}", fr.http_status, fr.duration_seconds)

    outcomes: list[CertificationOutcome] = []
    if ev.status.profile_was_read:
        done = {c.template_id: c for c in ev.completed}
        for row in ev.result_rows():
            tid = row["template_id"]
            if tid not in cert_ids:
                raise InvalidRecordError(f"Course template {tid} is not an active required certification.")
            c = done.get(tid)
            evidence = None if c is None else {
                "credential_title": c.credential_title, "match_method": c.match_method,
                "earned": c.earned_text, "extra_credential_ids": list(c.extra_credential_ids),
                "parser_version": ev.parser_version, "evaluator_version": ev.evaluator_version,
                "html": c.evidence,
            }
            outcomes.append(CertificationOutcome(cert_ids[tid], bool(row["completed"]),
                                                 row["credential_id"], evidence))

    diagnostic = prefix + (ev.diagnostic or fr.diagnostic or "")
    return VerificationInput(
        student_id=student_id, campaign_id=campaign_id, status=ev.status, mode=mode,
        http_status=fr.http_status, error_category=ev.error_category or fr.error_category,
        diagnostic_message=diagnostic[:_DIAG_LIMIT] or None, duration_seconds=fr.duration_seconds,
        outcomes=tuple(outcomes),
    )