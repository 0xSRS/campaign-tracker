"""Run many profile checks with graceful cancellation. No database access here."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.exceptions import InvalidProfileURLError
from scraper.controlled_fetcher import ControlledFetcher, ControlledFetchResult

logger = logging.getLogger("scraper.control")


class BatchState(StrEnum):
    COMPLETED = "COMPLETED"  # final fetch result available
    INVALID_URL = "INVALID_URL"  # permanent validation error; never requested
    CANCELLED = "CANCELLED"  # interrupted; NOT a verification result
    NOT_STARTED = "NOT_STARTED"  # batch stopped (cancel / rate-limit breaker) before this item


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    key: Hashable
    url: str
    state: BatchState
    fetch: ControlledFetchResult | None = None
    detail: str | None = None


class VerificationBatch:
    """`on_outcome` is awaited for COMPLETED and INVALID_URL items only, one at a time as each
    finishes, so the caller can persist each in its own transaction. CANCELLED / NOT_STARTED items
    are returned but never passed to it: nothing should be stored for them."""

    def __init__(self, controlled: ControlledFetcher, limiter, *, max_consecutive_rate_limits: int = 3,
                 cancel: asyncio.Event | None = None) -> None:
        self._controlled, self._limiter = controlled, limiter
        self._max_429 = max_consecutive_rate_limits
        self._cancel = cancel or asyncio.Event()

    def cancel(self) -> None:
        logger.info("batch cancellation requested")
        self._cancel.set()

    async def run(self, items: Sequence[tuple[Hashable, str]],
                  on_outcome: Callable[[BatchOutcome], Awaitable[None]] | None = None) -> list[BatchOutcome]:
        queue = deque(enumerate(items))
        outcomes: dict[int, BatchOutcome] = {}
        state = {"consecutive": 0, "tripped": False}

        async def worker() -> None:
            while queue and not self._cancel.is_set() and not state["tripped"]:
                idx, (key, url) = queue.popleft()
                try:
                    try:
                        fr = await self._controlled.fetch(url, self._cancel)
                    except InvalidProfileURLError as exc:
                        out = BatchOutcome(key, url, BatchState.INVALID_URL, None, str(exc))
                        state["consecutive"] = 0
                    else:
                        if fr.cancelled:
                            out = BatchOutcome(key, url, BatchState.CANCELLED, fr, "cancelled")
                        else:
                            out = BatchOutcome(key, url, BatchState.COMPLETED, fr)
                            state["consecutive"] = state["consecutive"] + 1 if fr.rate_limited else 0
                            if state["consecutive"] >= self._max_429 and not state["tripped"]:
                                state["tripped"] = True
                                logger.error("batch stopped: %d consecutive rate-limited profiles", state["consecutive"])
                    outcomes[idx] = out
                    if on_outcome and out.state in (BatchState.COMPLETED, BatchState.INVALID_URL):
                        await on_outcome(out)
                except Exception:
                    self._cancel.set()  # stop cleanly; finish nothing half-written
                    raise

        results = await asyncio.gather(*(worker() for _ in range(self._limiter.config.max_concurrency)),
                                       return_exceptions=True)
        reason = "rate_limit_circuit_open" if state["tripped"] else "cancelled"
        for idx, (key, url) in queue:
            outcomes[idx] = BatchOutcome(key, url, BatchState.NOT_STARTED, None, reason)
        for r in results:
            if isinstance(r, BaseException):
                raise r
        return [outcomes[i] for i in sorted(outcomes)]