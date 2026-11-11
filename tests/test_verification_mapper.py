"""Fetch result -> storable verification. Mocked fetch results, real parser + evaluator, no DB."""

from datetime import UTC, datetime

from core.constants import VerificationMode, VerificationStatus as V
from core.exceptions import InvalidRecordError
from scraper.batch import BatchOutcome, BatchState
from scraper.controlled_fetcher import ControlledFetchResult
from scraper.models import AccessClass as A, FetchResult
from scraper.parser import parse_profile
from services.evaluator import evaluate
from services.verification_mapper import build_verification_input
from tests.profile_html import REAL, build, campaign

C = campaign()
NOW = datetime(2026, 11, 11, tzinfo=UTC)
URL = "https://www.skills.google/public_profiles/abc"
CERT_IDS = {c.template_id: 100 + i for i, c in enumerate(C.courses)}


def fetch(access=A.ACCESSIBLE, status=200, cat=None, html=None, attempts=1):
    fr = FetchResult(URL, URL, status, {}, html, None, NOW, 0.5, access, cat, "diag")
    return BatchOutcome(1, URL, BatchState.COMPLETED, ControlledFetchResult(fr, False, attempts, (), 0, "x"))


def mk(outcome, parser=parse_profile, evaluator=evaluate, cert_ids=CERT_IDS):
    return build_verification_input(student_id=1, campaign_id=1, mode=VerificationMode.BULK, outcome=outcome,
                                    campaign=C, cert_ids=cert_ids, parser=parser, evaluator=evaluator, now=NOW)


def test_completed_has_all_outcomes_with_evidence():
    vi = mk(fetch(html=build(REAL)))
    assert vi.status is V.COMPLETED and len(vi.outcomes) == 10 and all(o.is_completed for o in vi.outcomes)
    assert all(o.matched_credential_id and o.evidence["html"] for o in vi.outcomes)
    assert {o.required_certification_id for o in vi.outcomes} == set(CERT_IDS.values())


def test_incomplete_records_missing_courses():
    vi = mk(fetch(html=build(REAL[:7])))
    assert vi.status is V.INCOMPLETE and sum(not o.is_completed for o in vi.outcomes) == 3
    assert all(o.evidence is None for o in vi.outcomes if not o.is_completed)


def test_empty_but_parsed_profile_is_incomplete_not_failed():
    vi = mk(fetch(html=build([])))
    assert vi.status is V.INCOMPLETE and len(vi.outcomes) == 10 and not any(o.is_completed for o in vi.outcomes)


def test_private_notfound_failed_are_never_incomplete():
    cases = ((A.PRIVATE_OR_INACCESSIBLE, V.PRIVATE_INACCESSIBLE), (A.NOT_FOUND, V.NOT_FOUND),
             (A.FAILED, V.FAILED), (A.NEEDS_REVIEW, V.NEEDS_REVIEW))
    for access, want in cases:
        vi = mk(fetch(access, 503, "server_error"))
        assert vi.status is want and vi.outcomes == () and vi.http_status == 503
        assert vi.error_category == "server_error"


def test_unparseable_page_needs_review_with_no_results():
    vi = mk(fetch(html="<html><script></script><body><div id='r'></div></body></html>"))
    assert vi.status is V.NEEDS_REVIEW and vi.outcomes == () and vi.error_category.startswith("parser_")


def test_parser_crash_is_stored_as_failed():
    def boom(*a, **k):
        raise RuntimeError("kaput")
    vi = mk(fetch(html=build(REAL)), parser=boom)
    assert vi.status is V.FAILED and vi.error_category == "evaluation_error" and vi.outcomes == ()


def test_invalid_url_and_unstorable_outcomes():
    vi = mk(BatchOutcome(1, "x", BatchState.INVALID_URL, None, "bad url"))
    assert vi.status is V.FAILED and vi.error_category == "invalid_url"
    for state in (BatchState.CANCELLED, BatchState.NOT_STARTED):
        try:
            mk(BatchOutcome(1, "x", state))
            raise AssertionError("must refuse")
        except InvalidRecordError:
            pass


def test_course_missing_from_active_certifications_is_refused():
    try:
        mk(fetch(html=build(REAL)), cert_ids={754: 1})
        raise AssertionError("must refuse")
    except InvalidRecordError:
        pass


def test_retry_attempts_are_noted_in_diagnostic():
    vi = mk(fetch(A.FAILED, 503, "server_error", attempts=3))
    assert vi.diagnostic_message.startswith("attempts=3")