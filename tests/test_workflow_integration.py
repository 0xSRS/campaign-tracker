"""Whole pipeline with the REAL fetcher (httpx MockTransport), parser, evaluator, database and exporter."""

from __future__ import annotations

import csv

import httpx
import pytest

from campaign.loader import load_campaign
from core.constants import CSV_HEADER, VerificationStatus as V
from database import Database
from scraper.controlled_fetcher import ControlledFetcher
from scraper.fetcher import FetcherConfig, ProfileFetcher
from scraper.retry import RetryPolicy
from scraper.throttle import RateLimiter, ThrottleConfig
from services.exporter import ReportExporter, ReportKind
from services.reports import CampaignReports
from services.verification_models import SelectionMode as M
from services.verification_service import VerificationService
from tests.profile_html import REAL, build

BASE = "https://www.skills.google/public_profiles/"


def html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body.encode(), headers={"content-type": "text/html; charset=utf-8"})


PAGES = {  # factories: a Response can only be streamed once
    "done": lambda: html(build(REAL)),
    "part": lambda: html(build(REAL[:3])),
    "gone": lambda: html("<html><body>Not found</body></html>", 404),
    "down": lambda: html("<html><body>Unavailable</body></html>", 503),
    "slow": lambda: httpx.ReadTimeout("timed out"),
    "weird": lambda: html("<html><body><div id='r'></div></body></html>"),
    "broken": lambda: html("<html><body><div class='x'><ul><li>unclosed"),
}


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def pipeline(tmp_path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    campaign = load_campaign()
    calls: list[str] = []

    def handler(request: httpx.Request):
        name = request.url.path.rsplit("/", 1)[-1]
        calls.append(name)
        item = PAGES[name]()
        if isinstance(item, Exception):
            raise item
        return item

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = ProfileFetcher(FetcherConfig(max_retries=0), client=client, sleep=_no_sleep)
        limiter = RateLimiter(ThrottleConfig(0.0, 1))
        controlled = ControlledFetcher(fetcher, limiter, RetryPolicy(max_retries=0, jitter=0.0))
        service = VerificationService(db, campaign, controlled, limiter)
        service.ensure_campaign()
        yield service, db, campaign, calls, tmp_path
    db.close()


async def test_csv_to_verification_to_exported_reports(pipeline):
    service, db, campaign, calls, tmp_path = pipeline
    text = CSV_HEADER + "\n" + "\n".join(BASE + n for n in [*PAGES, "done"]) + "\n"  # "done" listed twice
    report = service.import_csv(text, is_text=True)
    assert report.stored.added == len(PAGES) and calls == []  # duplicate collapsed, import makes no requests

    await service.run(service.preview(M.ALL), confirmed=True)
    assert sorted(set(calls)) == sorted(PAGES) and len(calls) == len(PAGES)

    reports = CampaignReports(db, campaign)
    status = {s.url.rsplit("/", 1)[1]: s.latest_status for s in reports.student_summary()}
    assert status["done"] is V.COMPLETED and status["part"] is V.INCOMPLETE
    assert status["down"] is V.FAILED and status["slow"] is V.FAILED
    assert status["gone"] in (V.NOT_FOUND, V.NEEDS_REVIEW)  # a bare 404 page may be held for review
    assert status["weird"] not in (V.COMPLETED, V.INCOMPLETE)  # unreadable page is never "0 courses"
    assert status["broken"] is not V.COMPLETED

    exporter = ReportExporter(reports, tmp_path / "out")
    with open(exporter.export(ReportKind.CAMPAIGN_SUMMARY).path, newline="", encoding="utf-8-sig") as fh:
        m = dict(list(csv.reader(fh))[1:])
    assert m["Total registered students"] == str(len(PAGES))
    assert m["Completed students"] == "1"
    unread = sum(int(m[k]) for k in ("Failed students", "Inaccessible students", "Not-found students",
                                     "Needs-review students"))
    assert unread >= 3 and int(m["Incomplete students"]) <= 2  # failures never leak into "incomplete"