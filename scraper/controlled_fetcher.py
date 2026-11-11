"""Wraps ProfileFetcher with rate limiting + retries.

The wrapped ProfileFetcher MUST be built with max_retries=0 (single attempt) so retries are
decided only here:  FetcherConfig.from_settings(s) then dataclasses.replace(cfg, max_retries=0).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from scraper.models import AccessClass, FetchResult
from scraper.retry import RetryPolicy
from scraper.throttle import AcquireCancelled, RateLimiter, interruptible_sleep

logger = logging.getLogger("scraper.control")


def _ref(url: str) -> str:
    """Short, non-identifying reference for logs (never the full URL)."""
    return "…" + urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1][-8:]


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt: int
    http_status: int | None
    error_category: str | None
    access: AccessClass
    decision: str
    backoff_seconds: float


@dataclass(frozen=True, slots=True)
class ControlledFetchResult:
    result: FetchResult | None  # last real result; kept even when cancelled mid-retry
    cancelled: bool
    attempts: int
    attempt_log: tuple[AttemptRecord, ...]
    rate_limit_events: int
    final_reason: str

    @property
    def rate_limited(self) -> bool:
        return self.result is not None and self.result.error_category == "rate_limited" and not self.cancelled


class ControlledFetcher:
    def __init__(self, fetcher, limiter: RateLimiter, policy: RetryPolicy | None = None, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rng: Callable[[], float] = random.random) -> None:
        self._fetcher, self._limiter = fetcher, limiter
        self._policy = policy or RetryPolicy()
        self._sleep, self._rng = sleep, rng

    async def fetch(self, url: str, cancel: asyncio.Event | None = None) -> ControlledFetchResult:
        """Raises InvalidProfileURLError for bad URLs (permanent; never retried)."""
        ref = _ref(url)
        log: list[AttemptRecord] = []
        attempt = rl_used = rl_events = 0
        last: FetchResult | None = None
        while True:
            attempt += 1
            try:
                async with self._limiter.slot(cancel):
                    logger.info("request start ref=%s attempt=%d", ref, attempt)
                    result = await self._fetcher.fetch(url)
            except AcquireCancelled:
                logger.info("cancelled before request ref=%s attempt=%d", ref, attempt)
                return ControlledFetchResult(last, True, attempt - 1, tuple(log), rl_events, "cancelled")
            logger.info("request complete ref=%s attempt=%d http=%s access=%s category=%s",
                        ref, attempt, result.http_status, result.access.value, result.error_category)

            d = self._policy.decide(result, attempt, rate_limit_retries_used=rl_used, rng=self._rng)
            if d.rate_limited:
                rl_events += 1
                self._limiter.pause_for(d.delay, f"HTTP {result.http_status} ref={ref}")
            log.append(AttemptRecord(attempt, result.http_status, result.error_category, result.access,
                                     d.reason, d.delay if d.retry else 0.0))
            result = replace(result, attempts=attempt)
            if not d.retry:
                if result.access is AccessClass.FAILED:
                    logger.warning("final failure ref=%s attempts=%d category=%s reason=%s",
                                   ref, attempt, result.error_category, d.reason)
                return ControlledFetchResult(result, False, attempt, tuple(log), rl_events, d.reason)

            if d.rate_limited:
                rl_used += 1
            logger.warning("retry ref=%s next_attempt=%d backoff=%.2fs reason=%s",
                           ref, attempt + 1, d.delay, d.reason)
            last = result
            if not await interruptible_sleep(self._sleep, d.delay, cancel):
                logger.info("cancelled during backoff ref=%s", ref)
                return ControlledFetchResult(last, True, attempt, tuple(log), rl_events, "cancelled")