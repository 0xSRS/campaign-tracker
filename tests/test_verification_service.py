"""Verification service integration: temp SQLite DB, mocked fetcher, real parser/evaluator."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from campaign.loader import load_campaign
from core.constants import CSV_HEADER, VerificationStatus as V
from core.exceptions import DatabaseError
from database import Database, StudentRepository, VerificationRepository
from scraper.controlled_fetcher import ControlledFetcher
from scraper.models import AccessClass as A, FetchResult
from scraper.retry import RetryPolicy
from scraper.throttle import RateLimiter, ThrottleConfig
from services.reports import CampaignReports
from services.verification_models import (
    ConfirmationRequired,
    ExclusionReason as R,
    SelectionMode as M,
)
from services.verification_service import VerificationService
from tests.profile_html import REAL, build

BASE = "https://www.skills.google/public_profiles/"
NOW = datetime(2026, 11, 11, tzinfo=UTC)


def url(name: str) -> str:
    return BASE + name


def result(u, access=A.ACCESSIBLE, status=200, cat=None, html=None):
    return FetchResult(u, u, status, {}, html, None, NOW, 0.1, access, cat, "diag")


def complete(u):
    return result(u, html=build(REAL))


def partial(u, n=5):
    return result(u, html=build(REAL[:n]))


class FakeFetcher:
    def __init__(self):
        self.results: dict[str, FetchResult] = {}
        self.calls: list[str] = []

    async def fetch(self, u):
        self.calls.append(u)
        return self.results[u]


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    campaign = load_campaign()
    fetcher = FakeFetcher()
    limiter = RateLimiter(ThrottleConfig(0.0, 1))
    controlled = ControlledFetcher(fetcher, limiter, RetryPolicy(max_retries=0, jitter=0.0))
    service = VerificationService(db, campaign, controlled, limiter, clock=lambda: NOW)
    yield SimpleNamespace(db=db, campaign=campaign, fetcher=fetcher, service=service)
    db.close()


def csv(*names):
    return CSV_HEADER + "\n" + "\n".join(url(n) for n in names) + "\n"


def student_id(env, name):
    with env.db.session_scope() as s:
        return StudentRepository(s).get_by_normalized_url(url(name)).id


def latest(env, name):
    with env.db.session_scope() as s:
        return StudentRepository(s).get(student_id(env, name)).latest_status


def history(env, name):
    with env.db.session_scope() as s:
        return VerificationRepository(s).history(student_id(env, name))


async def verify(env, mode=M.ALL, **kw):
    return await env.service.run(env.service.preview(mode), confirmed=True, **kw)


def seed(env):
    """One student per outcome; returns names."""
    env.service.import_csv(csv("done", "part", "priv", "gone", "down", "weird", "slow"), is_text=True)
    f = env.fetcher.results
    f[url("done")] = complete(url("done"))
    f[url("part")] = partial(url("part"))
    f[url("priv")] = result(url("priv"), A.PRIVATE_OR_INACCESSIBLE, 200, "redirected_to_login")
    f[url("gone")] = result(url("gone"), A.NOT_FOUND, 404, "not_found")
    f[url("down")] = result(url("down"), A.FAILED, 503, "server_error")
    f[url("weird")] = result(url("weird"), html="<html><body><div id='r'></div></body></html>")
    f[url("slow")] = result(url("slow"), A.FAILED, None, "timeout")


# ---- import ---------------------------------------------------------------------
async def test_new_csv_import_adds_students_and_makes_no_requests(env):
    rep = env.service.import_csv(csv("a", "b", "c"), is_text=True)
    assert rep.stored.added == 3 and len(rep.new_student_ids) == 3 and rep.existing_student_ids == ()
    assert env.fetcher.calls == []
    assert env.service.preview(M.ALL).total_registered == 3


async def test_existing_csv_import_skips_reports_duplicates_and_never_deletes(env):
    env.service.import_csv(csv("a", "b", "c"), is_text=True)
    env.fetcher.results.update({url(n): complete(url(n)) for n in "abc"})
    await verify(env)
    calls = len(env.fetcher.calls)

    rep = env.service.import_csv(csv("a", "b", "d", "d"), is_text=True)
    s = rep.result.summary
    assert s.existing_profiles == 2 and s.new_profiles == 1 and s.duplicate_rows >= 1
    assert rep.stored.added == 1 and len(rep.existing_student_ids) == 2
    assert len(env.fetcher.calls) == calls  # import never scrapes

    env.service.import_csv(csv("a"), is_text=True)  # omitting students must not delete them
    assert env.service.preview(M.ALL).total_registered == 4
    assert len(history(env, "a")) == 1  # history preserved


async def test_reverify_existing_is_a_separate_explicit_step(env):
    env.service.import_csv(csv("a", "b"), is_text=True)
    rep = env.service.import_csv(csv("a", "b", "c"), is_text=True)
    p = env.service.preview(M.ALL, student_ids=rep.existing_student_ids)
    assert p.eligible_count == 2 and p.excluded[0].reason is R.NOT_IN_SUBSET


# ---- dry run / confirmation -----------------------------------------------------------
async def test_dry_run_reports_without_requests_and_requires_confirmation(env):
    seed(env)
    p = env.service.preview(M.ALL)
    assert p.total_registered == 7 and p.eligible_count == 7 and p.excluded_count == 0
    with pytest.raises(ConfirmationRequired):
        await env.service.run(p, confirmed=False)
    assert env.fetcher.calls == []
    await env.service.run(p, confirmed=True)
    b = env.service.preview(M.INCOMPLETE)
    assert b.eligible_count == 1 and b.excluded_count == 6 and b.mode is M.INCOMPLETE
    assert R.ALREADY_COMPLETED in b.exclusion_counts


# ---- outcomes -------------------------------------------------------------------------
async def test_every_outcome_is_stored_distinctly_and_failures_are_not_incomplete(env):
    seed(env)
    out = await verify(env)
    assert out.progress.state == "finished" and out.progress.remaining == 0
    p = out.progress
    assert (p.completed, p.incomplete, p.failed, p.inaccessible, p.not_found, p.needs_review) == (1, 1, 2, 1, 1, 1)
    assert latest(env, "done") is V.COMPLETED and latest(env, "part") is V.INCOMPLETE
    assert latest(env, "priv") is V.PRIVATE_INACCESSIBLE and latest(env, "gone") is V.NOT_FOUND
    assert latest(env, "down") is V.FAILED and latest(env, "slow") is V.FAILED
    assert latest(env, "weird") is V.NEEDS_REVIEW  # parser could not read the page
    for name in ("priv", "gone", "down", "weird", "slow"):
        rec = history(env, name)[0]
        assert rec.completed_count is None and rec.missing_count is None
    with env.db.session_scope() as s:
        rec = VerificationRepository(s).history(student_id(env, "part"))[0]
        rows = VerificationRepository(s).results(rec.id)
        assert rec.completed_count == 5 and rec.missing_count == 5 and len(rows) == 10
        assert any(r.evidence and r.matched_credential_id for r in rows if r.is_completed)


# ---- modes ----------------------------------------------------------------------------
async def test_mode_a_rechecks_completed_students_and_keeps_history(env):
    seed(env)
    await verify(env)
    before = len(env.fetcher.calls)
    await verify(env, M.ALL)
    assert len(env.fetcher.calls) == before + 7 and len(history(env, "done")) == 2


async def test_mode_b_only_incomplete(env):
    seed(env)
    await verify(env)
    env.fetcher.calls.clear()
    out = await verify(env, M.INCOMPLETE)
    assert env.fetcher.calls == [url("part")] and out.progress.total_selected == 1


async def test_mode_c_only_retryable_failures_then_recovers(env):
    seed(env)
    await verify(env)
    p = env.service.preview(M.RETRY_FAILED)
    assert sorted(r.url for r in p.eligible) == sorted([url("down"), url("slow")])
    ex = {r.url: r.reason for r in p.excluded}
    assert ex[url("priv")] is R.PRIVATE_OR_INACCESSIBLE and ex[url("gone")] is R.NOT_FOUND
    assert ex[url("weird")] is R.NEEDS_REVIEW and ex[url("done")] is R.LATEST_EVALUATED_OK
    env.fetcher.results[url("down")] = complete(url("down"))
    env.fetcher.results[url("slow")] = partial(url("slow"), 3)
    await env.service.run(p, confirmed=True)
    assert latest(env, "down") is V.COMPLETED and latest(env, "slow") is V.INCOMPLETE
    assert len(history(env, "down")) == 2 and history(env, "down")[1].status is V.FAILED
    assert env.service.preview(M.RETRY_FAILED).eligible_count == 0


# ---- failures of the machinery ----------------------------------------------------------
async def test_parser_failure_is_failed_not_incomplete(env):
    env.service.import_csv(csv("a"), is_text=True)
    env.fetcher.results[url("a")] = complete(url("a"))

    def boom(*a, **k):
        raise RuntimeError("parser bug")
    env.service._parser = boom
    await verify(env)
    assert latest(env, "a") is V.FAILED and history(env, "a")[0].error_category == "evaluation_error"


async def test_database_failure_stops_cleanly_and_keeps_committed_records(env, monkeypatch):
    env.service.import_csv(csv("a", "b", "c"), is_text=True)
    env.fetcher.results.update({url(n): complete(url(n)) for n in "abc"})
    real, n = VerificationRepository.record, {"n": 0}

    def flaky(self, data):
        n["n"] += 1
        if n["n"] == 2:
            raise DatabaseError("disk full")
        return real(self, data)
    monkeypatch.setattr(VerificationRepository, "record", flaky)
    with pytest.raises(DatabaseError):
        await verify(env)
    assert env.service.last_progress.state == "error" and env.service.last_progress.processed == 1
    assert len(env.fetcher.calls) == 2  # third student never requested
    assert latest(env, "a") is V.COMPLETED and latest(env, "b") is None and latest(env, "c") is None


async def test_cancellation_stores_nothing_for_unprocessed_students(env):
    env.service.import_csv(csv("a", "b", "c", "d"), is_text=True)
    env.fetcher.results.update({url(x): complete(url(x)) for x in "abcd"})

    def on_progress(snap):
        if snap.processed == 1:
            env.service.cancel()
    out = await verify(env, on_progress=on_progress)
    assert out.progress.state == "cancelled" and out.not_started == 3 and out.progress.remaining == 3
    assert latest(env, "a") is V.COMPLETED and all(latest(env, x) is None for x in "bcd")
    assert len(out.record_ids) == 1


# ---- reports ----------------------------------------------------------------------------
async def test_report_queries(env):
    seed(env)
    await verify(env)
    rep = CampaignReports(env.db, env.campaign)
    stats = rep.statistics()
    assert stats.registered == 7 and stats.never_verified == 0
    assert stats.evaluated_students == 2 and stats.completed_students == 1 and stats.incomplete_students == 1
    assert stats.completion_rate == 50.0 and stats.by_latest_status["FAILED"] == 2
    assert {f.status for f in rep.failed_records()} == {
        V.FAILED, V.PRIVATE_INACCESSIBLE, V.NOT_FOUND, V.NEEDS_REVIEW}
    assert len(rep.failed_records()) == 5
    miss = rep.missing_certifications()
    assert len(miss) == 1 and len(miss[0].missing_courses) == 5 and miss[0].evaluation_is_current
    courses = rep.course_completion()
    assert len(courses) == 10 and all(c.evaluated_students == 2 for c in courses)
    assert sum(c.completed_students for c in courses) == 15  # 10 + 5
    summary = {s.url: s for s in rep.student_summary()}
    assert summary[url("done")].completed_count == 10 and summary[url("priv")].completed_count is None