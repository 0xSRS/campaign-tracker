"""Centralised request scheduling: spacing, concurrency cap, global pause. No retry logic here."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Settings

logger = logging.getLogger("scraper.control")

MAX_CONCURRENCY = 3  # hard ceiling; this tool must not be aggressive


class AcquireCancelled(Exception):
    """The batch was cancelled while waiting for a request slot."""


@dataclass(frozen=True, slots=True)
class ThrottleConfig:
    # Our own conservative defaults, NOT an official Google Skills limit.
    min_interval_seconds: float = 2.0
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be >= 0")
        if not 1 <= self.max_concurrency <= MAX_CONCURRENCY:
            raise ValueError(f"max_concurrency must be between 1 and {MAX_CONCURRENCY}")

    @classmethod
    def from_settings(cls, s: Settings) -> ThrottleConfig:
        return cls(min_interval_seconds=s.request_delay_seconds, max_concurrency=s.max_concurrency)


async def interruptible_sleep(sleep: Callable[[float], Awaitable[None]], seconds: float,
                              cancel: asyncio.Event | None = None) -> bool:
    """Sleep `seconds`. Returns True if finished, False if `cancel` fired first."""
    if cancel is None:
        await sleep(seconds)
        return True
    if cancel.is_set():
        return False
    s = asyncio.ensure_future(sleep(seconds))
    c = asyncio.ensure_future(cancel.wait())
    try:
        await asyncio.wait({s, c}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (s, c):
            if not t.done():
                t.cancel()
    if s.done():
        s.result()
        return True
    return False


class RateLimiter:
    def __init__(self, config: ThrottleConfig | None = None, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.config = config or ThrottleConfig()
        self._clock, self._sleep = clock, sleep
        self._sem = asyncio.Semaphore(self.config.max_concurrency)
        self._lock = asyncio.Lock()
        self._next_start = 0.0
        self._paused_until = 0.0
        self.pause_events = 0
        self.requests_started = 0

    async def acquire(self, cancel: asyncio.Event | None = None) -> None:
        await self._sem.acquire()
        try:
            async with self._lock:  # serialises start times
                while True:
                    if cancel is not None and cancel.is_set():
                        raise AcquireCancelled
                    wait = max(self._next_start, self._paused_until) - self._clock()
                    if wait <= 0:
                        break
                    if not await interruptible_sleep(self._sleep, wait, cancel):
                        raise AcquireCancelled
                self._next_start = self._clock() + self.config.min_interval_seconds
                self.requests_started += 1
        except BaseException:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()

    @asynccontextmanager
    async def slot(self, cancel: asyncio.Event | None = None) -> AsyncIterator[None]:
        await self.acquire(cancel)
        try:
            yield
        finally:
            self.release()

    def pause_for(self, seconds: float, reason: str = "rate_limited") -> None:
        """Block ALL new request starts for `seconds` (extends, never shortens, an existing pause)."""
        self._paused_until = max(self._paused_until, self._clock() + max(0.0, seconds))
        self.pause_events += 1
        logger.warning("rate-limit pause %.1fs (%s)", seconds, reason)