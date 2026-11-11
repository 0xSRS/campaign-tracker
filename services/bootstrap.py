"""Wires the backend together. No Textual imports; the UI receives one AppServices object."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from campaign.schemas import CampaignConfig
from core.config import Settings, get_settings
from database import Database
from scraper.controlled_fetcher import ControlledFetcher
from scraper.fetcher import FetcherConfig, ProfileFetcher
from scraper.retry import RetryPolicy
from scraper.throttle import RateLimiter, ThrottleConfig
from services.reports import CampaignReports
from services.verification_service import VerificationService


@dataclass
class AppServices:
    campaign: CampaignConfig
    database: Database
    service: VerificationService
    reports: CampaignReports
    settings: Settings
    closer: Callable[[], Awaitable[Any]] | None = None

    @staticmethod
    async def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run blocking service/DB work off the event loop so the UI stays responsive."""
        return await asyncio.to_thread(lambda: fn(*args, **kwargs))

    async def close(self) -> None:
        if self.closer:
            await self.closer()
        self.database.close()


def build_services(campaign: CampaignConfig, settings: Settings | None = None) -> AppServices:
    settings = settings or get_settings()
    database = Database.from_settings(settings)
    database.initialize()
    fetcher = ProfileFetcher(replace(FetcherConfig.from_settings(settings), max_retries=0))
    limiter = RateLimiter(ThrottleConfig.from_settings(settings))
    controlled = ControlledFetcher(fetcher, limiter, RetryPolicy.from_settings(settings))
    service = VerificationService(database, campaign, controlled, limiter)
    service.ensure_campaign()
    return AppServices(campaign, database, service, CampaignReports(database, campaign), settings, fetcher.aclose)