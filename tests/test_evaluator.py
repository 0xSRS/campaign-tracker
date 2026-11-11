"""Evaluator tests: offline, deterministic (fixed timestamp)."""

from datetime import UTC, datetime
from types import SimpleNamespace as N

from core.constants import VerificationStatus as V
from scraper.models import AccessClass as A
from scraper.parser import ExtractionResult, ExtractionStatus, parse_profile
from services.evaluator import evaluate
from tests.profile_html import REAL, build, campaign

C = campaign()
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def ev(badges, camp=C, **kw):
    return evaluate(camp, access=A.ACCESSIBLE, extraction=parse_profile(build(badges, **kw), C), evaluated_at=NOW)


def test_10_of_10():
    r = ev(REAL)
    assert r.status is V.COMPLETED and (r.completed_count, r.missing_count, r.progress_percent) == (10, 0, 100.0)
    assert r.evaluated_at == NOW and all(c.evidence and c.credential_id for c in r.completed)


def test_9_of_10():
    r = ev(REAL[:9])
    assert r.status is V.INCOMPLETE and r.completed_count == 9 and [m.template_id for m in r.missing] == [754]
    assert r.progress_percent == 90.0


def test_5_1_0_of_10():
    for n, pct in ((5, 50.0), (1, 10.0)):
        r = ev(REAL[:n])
        assert r.status is V.INCOMPLETE and r.completed_count == n and r.progress_percent == pct
    r0 = ev([])
    assert r0.status is V.INCOMPLETE and r0.completed_count == 0 and r0.missing_count == 10  # parsed empty


def test_summary_example():
    assert ev(REAL[:7]).summary_lines() == ["Total required: 10", "Completed: 7", "Missing: 3",
                                            "Progress: 70%", "Status: INCOMPLETE"]


def test_duplicates_never_increase_count():
    r = ev([REAL[0], REAL[0], ("Explore Generative AI in Agent Platform", "999", 959)])
    assert r.completed_count == 1 and r.completed[0].extra_credential_ids == ("999",)


def test_unknown_credentials_not_counted():
    r = ev([("Other Badge", "1", 5000)] + REAL[:2])
    assert r.completed_count == 2 and r.unmatched_titles == ("Other Badge",)


def test_conflicting_template_not_counted_and_flagged_for_review():
    r = ev([("The Basics of Google Cloud Compute", "5", 99999)] + REAL[:3])
    assert r.status is V.NEEDS_REVIEW and r.completed_count is None and r.to_record_fields()["completed_count"] is None


def test_conflict_irrelevant_when_all_done():
    r = ev(REAL + [("The Basics of Google Cloud Compute", "5", 99999)])
    assert r.status is V.COMPLETED


def test_ambiguous_title_needs_review():
    camp = N(courses=[N(name="Intro", template_id=1), N(name="intro", template_id=2)])
    ex = parse_profile(build([("Intro", "9", 3)], with_dialogs=False), camp)
    r = evaluate(camp, access=A.ACCESSIBLE, extraction=ex, evaluated_at=NOW)
    assert r.status is V.NEEDS_REVIEW and r.completed_count is None


def test_failed_extraction_is_never_zero():
    bad = parse_profile("<<<>>>", C)
    shell = parse_profile("<html><script></script><body><div id='r'></div></body></html>", C)
    err = ExtractionResult(status=ExtractionStatus.PARSER_ERROR, parser_version="1", credentials=None, error="boom")
    for ex, want in ((bad, V.NEEDS_REVIEW), (shell, V.NEEDS_REVIEW), (err, V.FAILED), (None, V.FAILED)):
        r = evaluate(C, access=A.ACCESSIBLE, extraction=ex, evaluated_at=NOW)
        assert r.status is want and r.completed_count is None and r.progress_percent is None
        assert r.result_rows() == [] and r.to_record_fields()["missing_count"] is None


def test_fetch_outcomes_stay_distinct():
    for a, want in ((A.PRIVATE_OR_INACCESSIBLE, V.PRIVATE_INACCESSIBLE), (A.NOT_FOUND, V.NOT_FOUND),
                    (A.FAILED, V.FAILED), (A.NEEDS_REVIEW, V.NEEDS_REVIEW)):
        r = evaluate(C, access=a, extraction=None, evaluated_at=NOW, fetch_category="x")
        assert r.status is want and r.completed_count is None and r.error_category == "x"


def test_campaign_config_change_is_honoured():
    ex = parse_profile(build(REAL), C)  # parsed against the 10-course list
    smaller = N(courses=C.courses[:3])
    r = evaluate(smaller, access=A.ACCESSIBLE, extraction=ex, evaluated_at=NOW)
    assert r.status is V.COMPLETED and r.total_required == 3 and r.completed_count == 3
    assert any("campaign_differs" in w for w in r.warnings)
    bigger = N(courses=list(C.courses) + [N(name="New Course", template_id=4242)])
    r2 = evaluate(bigger, access=A.ACCESSIBLE, extraction=ex, evaluated_at=NOW)
    assert r2.status is V.INCOMPLETE and r2.total_required == 11 and r2.completed_count == 10


def test_db_handoff_fields_and_determinism():
    r = ev(REAL[:7])
    f = r.to_record_fields()
    assert f["status"] is V.INCOMPLETE and f["completed_count"] == 7 and f["missing_count"] == 3
    assert f["verified_at"] == NOW and len(r.result_rows()) == 10
    assert r == ev(REAL[:7])