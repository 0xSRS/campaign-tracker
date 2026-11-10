"""Reusable HTTPX client that fetches one public Google Skills profile page.

Public pages only: no auth, no captcha handling, no evasion. If the site blocks
us, the result is recorded and we stop.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import httpx

from core.exceptions import InvalidProfileURLError
from scraper.classifier import classify
from scraper.models import AccessClass, FetchResult

if TYPE_CHECKING:
    from core.config import Settings

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"skills.google", "www.skills.google"})
LOGIN_HOSTS = frozenset({"accounts.google.com", "accounts.youtube.com"})
LOGIN_PATH_TOKENS = ("signin", "sign-in", "login", "servicelogin")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
KEEP_HEADERS = frozenset(
    {"content-type", "content-length", "content-language", "cache-control",
     "retry-after", "server", "date", "location", "x-robots-tag"}
)
RETRYABLE = frozenset({"timeout", "dns_failure", "connection_error", "network_error", "server_error"})
_PREFIX = "/public_profiles/"


@dataclass(frozen=True, slots=True)
class FetcherConfig:
    connect_timeout: float = 10.0
    read_timeout: float = 20.0
    max_response_bytes: int = 2_000_000
    max_redirects: int = 5
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    user_agent: str = "LearnWithCommunitiesCampaignTracker/0.1 (public-profile check)"

    @classmethod
    def from_settings(cls, s: Settings) -> FetcherConfig:
        return cls(
            connect_timeout=s.fetch_connect_timeout_seconds,
            read_timeout=s.request_timeout_seconds,
            max_response_bytes=s.fetch_max_response_bytes,
            max_redirects=s.fetch_max_redirects,
            max_retries=s.max_retries,
            retry_backoff_seconds=s.fetch_retry_backoff_seconds,
            user_agent=s.user_agent,
        )


@dataclass(frozen=True, slots=True)
class _Raw:
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool
    encoding: str | None


def _profile_id(url: str) -> str | None:
    """Profile ID if url is an https skills.google public-profile URL, else None."""
    try:
        sp = urlsplit(url)
        port = sp.port
    except ValueError:
        return None
    if sp.scheme != "https" or sp.hostname not in ALLOWED_HOSTS or port not in (None, 443):
        return None
    if sp.username or sp.password:
        return None
    path = sp.path.rstrip("/")
    if not path.startswith(_PREFIX):
        return None
    pid = path[len(_PREFIX):]
    return pid if pid and "/" not in pid else None


def _is_login_redirect(url: str) -> bool:
    sp = urlsplit(url)
    return (sp.hostname or "") in LOGIN_HOSTS or any(t in sp.path.lower() for t in LOGIN_PATH_TOKENS)


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__  # type: ignore[assignment]


def _describe_error(exc: BaseException) -> tuple[str, str]:
    causes = list(_chain(exc))
    text = " | ".join(str(e) for e in causes).lower()
    if isinstance(exc, httpx.TimeoutException):
        category = "timeout"
    elif isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        category = "invalid_url"
    elif any(isinstance(e, ssl.SSLError) for e in causes) or "certificate verify failed" in text:
        category = "ssl_error"
    elif any(isinstance(e, socket.gaierror) for e in causes) or any(
        s in text for s in ("getaddrinfo failed", "name or service not known", "nodename nor servname",
                            "temporary failure in name resolution")
    ):
        category = "dns_failure"
    elif isinstance(exc, httpx.ConnectError):
        category = "connection_error"
    elif isinstance(exc, httpx.TransportError):
        category = "network_error"
    else:
        category = "unexpected_error"
    return category, f"{exc.__class__.__name__}: {str(exc)[:200]}"


def _decode(body: bytes, encoding: str | None) -> str:
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _excerpt(text: str) -> str:
    return " ".join(text[:2000].split())[:500]


class ProfileFetcher:
    """Fetches profile pages. Safe for concurrent use; call aclose() when done."""

    def __init__(
        self,
        config: FetcherConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config or FetcherConfig()
        self._client = client
        self._owns_client = client is None
        self._transport = transport
        self._sleep = sleep

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                follow_redirects=False,
                verify=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ProfileFetcher:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one profile. Pass the student's normalized URL. Never raises on network errors."""
        profile_id = _profile_id(url)
        if profile_id is None:
            raise InvalidProfileURLError(f"Not a Google Skills public profile URL: {url!r}")
        fetched_at = datetime.now(UTC)
        start = perf_counter()
        attempt = 0
        while True:
            attempt += 1
            result = await self._attempt(url, profile_id)
            if result.error_category in RETRYABLE and attempt <= self._config.max_retries:
                logger.debug("Retrying fetch (%s), attempt %d", result.error_category, attempt)
                await self._sleep(self._config.retry_backoff_seconds * 2 ** (attempt - 1))
                continue
            break
        logger.debug("Fetch %s -> %s (%s)", url, result.access, result.error_category)
        return replace(result, fetched_at=fetched_at, duration_seconds=perf_counter() - start, attempts=attempt)

    # -- internals ------------------------------------------------------------
    def _result(
        self, url: str, *, access: AccessClass, category: str | None, message: str,
        final_url: str | None = None, status: int | None = None, headers: dict[str, str] | None = None,
        html: str | None = None, excerpt: str | None = None, chain: list[str] | None = None,
        unfollowed: str | None = None,
    ) -> FetchResult:
        return FetchResult(
            requested_url=url, final_url=final_url, http_status=status, headers=headers or {},
            html=html, body_excerpt=excerpt, fetched_at=datetime.now(UTC), duration_seconds=0.0,
            access=access, error_category=category, diagnostic=message,
            redirect_chain=tuple(chain or ()), unfollowed_redirect=unfollowed,
        )

    async def _request(self, url: str) -> _Raw:
        cfg = self._config
        timeout = httpx.Timeout(connect=cfg.connect_timeout, read=cfg.read_timeout,
                                write=cfg.read_timeout, pool=cfg.connect_timeout)
        request_headers = {"User-Agent": cfg.user_agent, "Accept": "text/html,application/xhtml+xml"}
        async with self._get_client().stream(
            "GET", url, headers=request_headers, timeout=timeout, follow_redirects=False
        ) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS}
            encoding = getattr(resp, "charset_encoding", None)
            if resp.status_code in REDIRECT_STATUSES:
                return _Raw(resp.status_code, headers, b"", False, encoding)
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > cfg.max_response_bytes:
                return _Raw(resp.status_code, headers, b"", True, encoding)
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > cfg.max_response_bytes:
                    return _Raw(resp.status_code, headers, b"", True, encoding)
            return _Raw(resp.status_code, headers, bytes(buf), False, encoding)

    async def _attempt(self, url: str, profile_id: str) -> FetchResult:
        chain = [url]
        current = url
        hops = 0
        while True:
            try:
                raw = await self._request(current)
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                category, message = _describe_error(exc)
                return self._result(url, access=AccessClass.FAILED, category=category, message=message,
                                    final_url=current, chain=chain)
            except Exception as exc:  # noqa: BLE001 - never let one profile crash a bulk run
                logger.exception("Unexpected error while fetching a profile")
                return self._result(url, access=AccessClass.FAILED, category="unexpected_error",
                                    message=f"{exc.__class__.__name__}: {str(exc)[:200]}",
                                    final_url=current, chain=chain)

            if raw.status not in REDIRECT_STATUSES:
                break
            hops += 1
            location = raw.headers.get("location")
            if not location:
                return self._result(url, access=AccessClass.NEEDS_REVIEW, category="redirect_without_location",
                                    message=f"HTTP {raw.status} redirect without Location header.",
                                    final_url=current, status=raw.status, headers=raw.headers, chain=chain)
            if hops > self._config.max_redirects:
                return self._result(url, access=AccessClass.FAILED, category="too_many_redirects",
                                    message=f"More than {self._config.max_redirects} redirects.",
                                    final_url=current, status=raw.status, headers=raw.headers, chain=chain)
            target = urljoin(current, location)
            if _is_login_redirect(target):
                return self._result(url, access=AccessClass.PRIVATE_OR_INACCESSIBLE, category="redirected_to_login",
                                    message=f"HTTP {raw.status}: redirected to a sign-in page (not followed).",
                                    final_url=current, status=raw.status, headers=raw.headers,
                                    chain=chain, unfollowed=target)
            target_id = _profile_id(target)
            if target_id is None or target_id.casefold() != profile_id.casefold():
                return self._result(url, access=AccessClass.NEEDS_REVIEW, category="redirected_away",
                                    message=f"HTTP {raw.status}: redirect leaves this profile (not followed).",
                                    final_url=current, status=raw.status, headers=raw.headers,
                                    chain=chain, unfollowed=target)
            chain.append(target)
            current = target

        if raw.truncated:
            return self._result(url, access=AccessClass.FAILED, category="response_too_large",
                                message=f"Response exceeded {self._config.max_response_bytes} bytes.",
                                final_url=current, status=raw.status, headers=raw.headers, chain=chain)

        text = _decode(raw.body, raw.encoding)
        c = classify(raw.status, raw.headers.get("content-type"), text)
        ok = c.access is AccessClass.ACCESSIBLE
        return self._result(
            url, access=c.access, category=c.category, message=f"HTTP {raw.status}: {c.message}",
            final_url=current, status=raw.status, headers=raw.headers,
            html=text if ok else None, excerpt=None if ok else _excerpt(text), chain=chain,
        )