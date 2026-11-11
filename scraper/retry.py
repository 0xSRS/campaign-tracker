"""Retry decisions. Pure: no I/O, no sleeping, no rate limiting."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from scraper.models import AccessClass, FetchResult

if TYPE_CHECKING:
    from core.config import Settings


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP-date); None if absent/invalid."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    delay: float  # seconds to wait before the next attempt (or length of rate-limit pause)
    reason: str
    rate_limited: bool = False


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 2  # retries after the first attempt -> max_retries + 1 attempts total
    base_backoff: float = 2.0
    max_backoff: float = 60.0
    jitter: float = 0.25  # delay is reduced by up to this fraction, never increased past the cap
    retry_statuses: frozenset[int] = frozenset({500, 502, 503, 504})
    retry_categories: frozenset[str] = frozenset({"timeout", "dns_failure", "connection_error", "network_error"})
    default_rate_limit_pause: float = 60.0  # used when a 429 has no Retry-After
    max_rate_limit_pause: float = 300.0
    max_rate_limit_retries: int = 1

    def __post_init__(self) -> None:
        if self.max_retries < 0 or self.max_rate_limit_retries < 0:
            raise ValueError("retry limits must be >= 0")
        if self.base_backoff < 0 or self.max_backoff < 0 or not 0 <= self.jitter <= 1:
            raise ValueError("invalid backoff/jitter")

    @classmethod
    def from_settings(cls, s: Settings) -> RetryPolicy:
        return cls(max_retries=s.max_retries, base_backoff=s.retry_base_backoff_seconds,
                   max_backoff=s.retry_max_backoff_seconds, jitter=s.retry_jitter,
                   default_rate_limit_pause=s.rate_limit_default_pause_seconds,
                   max_rate_limit_pause=s.rate_limit_max_pause_seconds,
                   max_rate_limit_retries=s.max_rate_limit_retries)

    def backoff(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        raw = min(self.max_backoff, self.base_backoff * 2 ** (attempt - 1))
        return raw * (1 - self.jitter * rng())

    def decide(self, result: FetchResult, attempt: int, *, rate_limit_retries_used: int = 0,
               rng: Callable[[], float] = random.random, now: datetime | None = None) -> RetryDecision:
        """`attempt` = attempts made so far (1-based)."""
        if result.access is not AccessClass.FAILED:
            return RetryDecision(False, 0.0, f"definitive_outcome:{result.access.value}")
        status, cat = result.http_status, result.error_category
        retry_after = parse_retry_after(result.headers.get("retry-after"), now)

        if status == 429 or cat == "rate_limited":
            wait = retry_after if retry_after is not None else self.default_rate_limit_pause
            pause = min(wait, self.max_rate_limit_pause)
            if rate_limit_retries_used >= self.max_rate_limit_retries:
                return RetryDecision(False, pause, "rate_limit_retries_exhausted", True)
            if wait > self.max_rate_limit_pause:
                return RetryDecision(False, pause, "retry_after_exceeds_max", True)
            return RetryDecision(True, wait, "rate_limited", True)

        retryable = status in self.retry_statuses or (status is None and cat in self.retry_categories)
        if not retryable:
            return RetryDecision(False, 0.0, f"not_retryable:{cat or status}")
        if attempt > self.max_retries:
            return RetryDecision(False, 0.0, "max_retries_exhausted")
        delay = self.backoff(attempt, rng)
        if retry_after is not None:  # e.g. 503 + Retry-After: never come back earlier than asked
            delay = max(delay, min(retry_after, self.max_rate_limit_pause))
        return RetryDecision(True, delay, cat or f"http_{status}")