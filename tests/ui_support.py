"""Shared fixtures for TUI tests: temp DB, mocked fetcher, real parser/evaluator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from campaign.loader import load_campaign
from core.constants import CSV_HEADER
from database import Database
from scraper.controlled_fetcher import ControlledFetcher
from scraper.models import AccessClass as A, FetchResult
from scraper.retry import RetryPolicy
from scraper.throttle import RateLimiter, ThrottleConfig
from services.bootstrap import AppServices
from services.reports import CampaignReports
from services.verification_service import VerificationService
from tests.profile_html import REAL, build

BASE = "https://www.skills.google/public_profiles/"
NOW = datetime(2026, 11, 11, tzinfo=UTC)


def url(name: str) -> str:
    return BASE + name


def make_csv(*names: str) -> str:
    return CSV_HEADER + "\n" + "\n".join(url(n) for n in names) + "\n"


def result(u, access=A.ACCESSIBLE, status=200, cat=None, html=None):
    return FetchResult(u, u, status, {}, html, None, NOW, 0.1, access, cat, "diag")


class FakeFetcher:
    def __init__(self):
        self.results: dict[str, FetchResult] = {}
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None  # set to hold requests until released

    async def fetch(self, u):
        self.calls.append(u)
        if self.gate is not None:
            await self.gate.wait()
        return self.results[u]


def build_env(tmp_path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    campaign = load_campaign()
    fetcher = FakeFetcher()
    limiter = RateLimiter(ThrottleConfig(0.0, 1))
    controlled = ControlledFetcher(fetcher, limiter, RetryPolicy(max_retries=0, jitter=0.0))
    service = VerificationService(db, campaign, controlled, limiter, clock=lambda: NOW)
    service.ensure_campaign()
    settings = SimpleNamespace(request_delay_seconds=0.0, database_path=db.path, log_file="-", log_level="INFO",
                               max_concurrency=1, request_timeout_seconds=1, max_retries=0,
                               rate_limit_default_pause_seconds=1)
    services = AppServices(campaign, db, service, CampaignReports(db, campaign), settings)
    return SimpleNamespace(db=db, campaign=campaign, fetcher=fetcher, service=service, services=services)


def seed_data(env):
    """done=COMPLETED, part=INCOMPLETE, priv=PRIVATE, gone=NOT_FOUND, down=FAILED(server_error); not yet verified."""
    env.service.import_csv(make_csv("done", "part", "priv", "gone", "down"), is_text=True)
    f = env.fetcher.results
    f[url("done")] = result(url("done"), html=build(REAL))
    f[url("part")] = result(url("part"), html=build(REAL[:5]))
    f[url("priv")] = result(url("priv"), A.PRIVATE_OR_INACCESSIBLE, 200, "redirected_to_login")
    f[url("gone")] = result(url("gone"), A.NOT_FOUND, 404, "not_found")
    f[url("down")] = result(url("down"), A.FAILED, 503, "server_error")


async def seed(env):
    """seed_data + one full verification run, so every outcome exists in history."""
    from services.verification_models import SelectionMode as M
    seed_data(env)
    await env.service.run(env.service.preview(M.ALL), confirmed=True)
    env.fetcher.calls.clear()