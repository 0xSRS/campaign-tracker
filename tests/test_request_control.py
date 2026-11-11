"""Rate limiter / retry / controlled fetcher / batch tests. Mocked HTTP, fake clock, no real sleeping."""

import asyncio
import logging
from datetime import UTC, datetime

from core.exceptions import InvalidProfileURLError
from scraper.batch import BatchState, VerificationBatch
from scraper.controlled_fetcher import ControlledFetcher
from scraper.models import AccessClass as A, FetchResult
from scraper.retry import RetryPolicy, parse_retry_after
from scraper.throttle import RateLimiter, ThrottleConfig

URL = "https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7"


class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        await asyncio.sleep(0)


def fr(access=A.FAILED, status=None, cat=None, headers=None):
    return FetchResult(URL, URL, status, headers or {}, "<html/>" if access is A.ACCESSIBLE else None, None,
                       datetime(2026, 11, 11, tzinfo=UTC), 0.0, access, cat, "diag")


OK = lambda: fr(A.ACCESSIBLE, 200)
S503 = lambda **h: fr(A.FAILED, 503, "server_error", h or None)
R429 = lambda **h: fr(A.FAILED, 429, "rate_limited", h or None)
TIMEOUT = lambda: fr(A.FAILED, None, "timeout")


class Stub:
    """Scripted fetcher; records the clock time of every call."""
    def __init__(self, clock, script):
        self.clock, self.script, self.times = clock, list(script), []

    async def fetch(self, url):
        self.times.append(self.clock.t)
        if "bad" in url:
            raise InvalidProfileURLError("bad")
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def build(script, *, interval=2.0, conc=1, policy=None, rng=lambda: 0.0):
    c = Clock()
    lim = RateLimiter(ThrottleConfig(interval, conc), clock=c.now, sleep=c.sleep)
    stub = Stub(c, script)
    cf = ControlledFetcher(stub, lim, policy or RetryPolicy(jitter=0.0), sleep=c.sleep, rng=rng)
    return c, lim, stub, cf


# --- rate limiter ---------------------------------------------------------
async def test_delay_between_request_starts():
    c = Clock()
    lim = RateLimiter(ThrottleConfig(2.0, 1), clock=c.now, sleep=c.sleep)
    starts = []
    for _ in range(3):
        async with lim.slot():
            starts.append(c.t)
    assert starts == [0.0, 2.0, 4.0]


async def test_concurrency_never_exceeds_limit():
    lim = RateLimiter(ThrottleConfig(0.0, 2))
    active = peak = 0

    async def job():
        nonlocal active, peak
        async with lim.slot():
            active += 1
            peak = max(peak, active)
            for _ in range(3):
                await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(*(job() for _ in range(6)))
    assert peak == 2


def test_config_validation_caps_concurrency():
    for kw in ({"max_concurrency": 4}, {"max_concurrency": 0}, {"min_interval_seconds": -1}):
        try:
            ThrottleConfig(**kw)
        except ValueError:
            continue
        raise AssertionError(kw)


async def test_pause_blocks_all_starts():
    c = Clock()
    lim = RateLimiter(ThrottleConfig(0.0, 1), clock=c.now, sleep=c.sleep)
    lim.pause_for(30)
    async with lim.slot():
        assert c.t == 30 and lim.pause_events == 1


# --- retry policy (pure) ----------------------------------------------------
def test_exponential_backoff_capped_and_jitter_bounded():
    p = RetryPolicy(base_backoff=2, max_backoff=5, jitter=0.5)
    assert [p.backoff(a, lambda: 0.0) for a in (1, 2, 3, 4)] == [2, 4, 5, 5]
    assert p.backoff(2, lambda: 0.999) >= 4 * 0.5 and p.backoff(2, lambda: 0.999) < 4


def test_decisions_retry_vs_permanent():
    p = RetryPolicy(jitter=0.0)
    for r in (S503(), fr(A.FAILED, 500, "server_error"), fr(A.FAILED, 502, "server_error"),
              fr(A.FAILED, 504, "server_error"), TIMEOUT(), fr(A.FAILED, None, "connection_error")):
        assert p.decide(r, 1).retry, r
    for r in (OK(), fr(A.PRIVATE_OR_INACCESSIBLE, 200, "x"), fr(A.NOT_FOUND, 404, "x"), fr(A.NEEDS_REVIEW, 200, "x"),
              fr(A.FAILED, None, "ssl_error"), fr(A.FAILED, None, "invalid_url"), fr(A.FAILED, 501, "server_error"),
              fr(A.FAILED, None, "response_too_large")):
        assert not p.decide(r, 1).retry, r
    assert not p.decide(S503(), 3).retry and p.decide(S503(), 3).reason == "max_retries_exhausted"


def test_retry_after_parsing():
    now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after("Wed, 11 Nov 2026 12:01:00 GMT", now) == 60.0
    assert parse_retry_after("Wed, 11 Nov 2020 12:01:00 GMT", now) == 0.0
    assert parse_retry_after("soon") is None and parse_retry_after(None) is None and parse_retry_after("") is None


# --- controlled fetcher -------------------------------------------------------
async def test_retry_then_success_with_exponential_backoff():
    c, _, stub, cf = build([S503(), S503(), OK()], interval=0)
    r = await cf.fetch(URL)
    assert r.result.access is A.ACCESSIBLE and r.attempts == 3 and r.result.attempts == 3
    assert c.sleeps == [2.0, 4.0] and [a.backoff_seconds for a in r.attempt_log] == [2.0, 4.0, 0.0]


async def test_max_retry_count_and_failure_preserved():
    c, _, stub, cf = build([S503()], interval=0, policy=RetryPolicy(max_retries=2, jitter=0.0))
    r = await cf.fetch(URL)
    assert len(stub.times) == 3 and r.attempts == 3 and r.final_reason == "max_retries_exhausted"
    assert r.result.error_category == "server_error" and r.result.http_status == 503 and not r.cancelled


async def test_permanent_outcomes_not_retried():
    for res in (fr(A.PRIVATE_OR_INACCESSIBLE, 200, "p"), fr(A.NOT_FOUND, 404, "n"), fr(A.FAILED, None, "ssl_error")):
        c, _, stub, cf = build([res], interval=0)
        r = await cf.fetch(URL)
        assert len(stub.times) == 1 and c.sleeps == [] and r.result.access is res.access
    c, _, stub, cf = build([OK()])
    try:
        await cf.fetch("https://bad")
        raise AssertionError("expected InvalidProfileURLError")
    except InvalidProfileURLError:
        pass


async def test_429_honours_retry_after_and_never_retries_immediately():
    c, lim, stub, cf = build([R429(**{"retry-after": "7"}), OK()], interval=0)
    r = await cf.fetch(URL)
    assert r.result.access is A.ACCESSIBLE and r.rate_limit_events == 1 and lim.pause_events == 1
    assert stub.times[1] - stub.times[0] >= 7 and 7.0 in c.sleeps


async def test_429_repeated_stops_after_one_retry():
    c, _, stub, cf = build([R429(**{"retry-after": "5"})], interval=0)
    r = await cf.fetch(URL)
    assert len(stub.times) == 2 and r.final_reason == "rate_limit_retries_exhausted" and r.rate_limited
    assert r.result.http_status == 429


async def test_429_retry_after_above_cap_is_not_retried():
    c, lim, stub, cf = build([R429(**{"retry-after": "9999"})], interval=0)
    r = await cf.fetch(URL)
    assert len(stub.times) == 1 and r.final_reason == "retry_after_exceeds_max" and lim.pause_events == 1


async def test_429_without_header_uses_default_pause():
    c, _, stub, cf = build([R429(), OK()], interval=0)
    await cf.fetch(URL)
    assert c.sleeps == [60.0]


async def test_cancel_during_backoff_keeps_last_failure():
    c, _, stub, cf = build([S503(), OK()], interval=0)
    cancel = asyncio.Event()

    async def sleep_and_cancel(s):
        cancel.set()
        await asyncio.sleep(0.01)  # cancel wins the race
    cf._sleep = sleep_and_cancel
    r = await cf.fetch(URL, cancel)
    assert r.cancelled and r.result.http_status == 503 and len(stub.times) == 1


# --- batch ----------------------------------------------------------------------
def batch_for(script, **kw):
    c, lim, stub, cf = build(script, interval=0, **kw)
    return c, stub, VerificationBatch(cf, lim)


async def test_batch_sequential_and_callbacks():
    c, stub, b = batch_for([OK()])
    seen = []

    async def cb(o):
        seen.append(o.key)
    out = await b.run([(i, URL) for i in range(3)], cb)
    assert [o.state for o in out] == [BatchState.COMPLETED] * 3 and seen == [0, 1, 2]


async def test_batch_cancel_stops_remaining_and_stores_nothing_for_them():
    c, stub, b = batch_for([OK()])
    seen = []

    async def cb(o):
        seen.append(o.key)
        b.cancel()  # cancel after the first finished item
    out = await b.run([(i, URL) for i in range(4)], cb)
    assert seen == [0] and [o.state for o in out] == [BatchState.COMPLETED] + [BatchState.NOT_STARTED] * 3
    assert len(stub.times) == 1


async def test_batch_rate_limit_circuit_breaker():
    c, stub, b = batch_for([R429(**{"retry-after": "1"})])
    out = await b.run([(i, URL) for i in range(8)])
    states = [o.state for o in out]
    assert states.count(BatchState.COMPLETED) == 3 and states.count(BatchState.NOT_STARTED) == 5
    assert out[-1].detail == "rate_limit_circuit_open"


async def test_batch_invalid_url_is_distinct_and_does_not_stop_batch():
    c, stub, b = batch_for([OK()])
    out = await b.run([(1, "https://bad"), (2, URL)])
    assert [o.state for o in out] == [BatchState.INVALID_URL, BatchState.COMPLETED]


async def test_callback_error_stops_batch_cleanly():
    c, stub, b = batch_for([OK()])

    async def cb(o):
        raise RuntimeError("db down")
    try:
        await b.run([(i, URL) for i in range(3)], cb)
        raise AssertionError("expected error")
    except RuntimeError:
        pass
    assert len(stub.times) == 1


# --- logging ----------------------------------------------------------------------
async def test_logging_events_without_full_url():
    recs = []

    class H(logging.Handler):
        def emit(self, r):
            recs.append(r.getMessage())
    log = logging.getLogger("scraper.control")
    h = H()
    log.addHandler(h)
    old = log.level
    log.setLevel(logging.INFO)
    try:
        c, lim, stub, cf = build([R429(**{"retry-after": "3"}), S503(), S503(), S503()], interval=0)
        await cf.fetch(URL)
    finally:
        log.removeHandler(h)
        log.setLevel(old)
    text = "\n".join(recs)
    for needle in ("request start", "request complete", "http=429", "rate-limit pause", "retry ref=", "backoff=",
                   "final failure"):
        assert needle in text, needle
    assert "be556f7b" not in text and "skills.google" not in text  # no full URL / profile id