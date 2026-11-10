import socket
import ssl

import httpx
import pytest

from core.config import Settings
from core.exceptions import InvalidProfileURLError
from scraper import AccessClass, FetcherConfig, ProfileFetcher

URL = "https://www.skills.google/public_profiles/abc-123"
OK_HTML = "<html><head><title>Profile</title></head><body><h1>Jane</h1><ul><li>A course</li></ul></body></html>"
A = AccessClass


def page(body: str, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, content=body.encode(), headers={"content-type": "text/html; charset=utf-8", **headers})


def sequence(*items):
    it = iter(items)

    def handler(request):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def always(item):
    def handler(request):
        if isinstance(item, Exception):
            raise item
        return item

    return handler


async def _nosleep(_: float) -> None:
    return None


async def run(handler, url=URL, **cfg):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as client:
        fetcher = ProfileFetcher(FetcherConfig(**cfg), client=client, sleep=_nosleep)
        result = await fetcher.fetch(url)
    return result, calls


async def test_success():
    r, calls = await run(always(page(OK_HTML)))
    assert r.access is A.ACCESSIBLE and r.error_category is None
    assert r.http_status == 200 and r.html == OK_HTML and r.body_excerpt is None
    assert r.requested_url == r.final_url == URL
    assert r.headers["content-type"].startswith("text/html")
    assert r.fetched_at and r.duration_seconds >= 0 and r.attempts == 1
    assert r.verification_status is None
    assert calls[0].headers["user-agent"] == FetcherConfig().user_agent


async def test_redirect_to_same_profile_is_followed():
    h = sequence(httpx.Response(302, headers={"location": "/public_profiles/abc-123/"}), page(OK_HTML))
    r, calls = await run(h)
    assert r.access is A.ACCESSIBLE and len(calls) == 2
    assert r.final_url == URL + "/" and r.requested_url == URL and len(r.redirect_chain) == 2


async def test_redirect_to_login_is_private_and_not_followed():
    target = "https://accounts.google.com/v3/signin"
    r, calls = await run(always(httpx.Response(302, headers={"location": target})))
    assert r.access is A.PRIVATE_OR_INACCESSIBLE and r.error_category == "redirected_to_login"
    assert r.unfollowed_redirect == target and len(calls) == 1


@pytest.mark.parametrize("target", ["https://example.com/x", "/public_profiles/someone-else", "/"])
async def test_redirect_away_needs_review(target):
    r, calls = await run(always(httpx.Response(302, headers={"location": target})))
    assert r.access is A.NEEDS_REVIEW and r.error_category == "redirected_away"
    assert r.unfollowed_redirect and len(calls) == 1


async def test_too_many_redirects():
    r, calls = await run(always(httpx.Response(302, headers={"location": "/public_profiles/abc-123"})), max_redirects=3)
    assert r.access is A.FAILED and r.error_category == "too_many_redirects" and len(calls) == 4


async def test_403_private_wording():
    r, _ = await run(always(page("<html><body><h1>This profile is private</h1></body></html>", 403)))
    assert r.access is A.PRIVATE_OR_INACCESSIBLE and r.http_status == 403
    assert r.html is None and "private" in r.body_excerpt.lower()


async def test_403_ambiguous_access_denied():
    r, _ = await run(always(page("<html><body><h1>Access Denied</h1></body></html>", 403)))
    assert r.access is A.NEEDS_REVIEW and r.error_category == "ambiguous_access_denied"


async def test_403_challenge_is_failed_and_not_retried():
    r, calls = await run(always(page("<html><body>Please complete the CAPTCHA</body></html>", 403)))
    assert r.access is A.FAILED and r.error_category == "blocked_by_site" and len(calls) == 1


async def test_404_with_not_found_wording():
    r, _ = await run(always(page("<html><body><h1>Profile not found</h1></body></html>", 404)))
    assert r.access is A.NOT_FOUND and r.error_category == "profile_not_found"


async def test_404_generic_needs_review():
    r, _ = await run(always(page("<html><body>Error</body></html>", 404)))
    assert r.access is A.NEEDS_REVIEW and r.error_category == "ambiguous_not_found"


async def test_429_not_retried_and_retry_after_kept():
    r, calls = await run(always(httpx.Response(429, headers={"retry-after": "30"})))
    assert r.access is A.FAILED and r.error_category == "rate_limited"
    assert len(calls) == 1 and r.headers["retry-after"] == "30"


async def test_500_retried_then_failed():
    r, calls = await run(always(httpx.Response(500)))
    assert r.access is A.FAILED and r.error_category == "server_error"
    assert len(calls) == 3 and r.attempts == 3


async def test_500_then_success_recovers():
    r, calls = await run(sequence(httpx.Response(500), page(OK_HTML)))
    assert r.access is A.ACCESSIBLE and r.attempts == 2 and len(calls) == 2


async def test_timeout():
    r, calls = await run(always(httpx.ReadTimeout("slow")))
    assert r.access is A.FAILED and r.error_category == "timeout" and len(calls) == 3
    assert r.http_status is None and r.html is None


async def test_connection_error():
    r, calls = await run(always(httpx.ConnectError("refused")))
    assert r.error_category == "connection_error" and len(calls) == 3


async def test_dns_failure():
    exc = httpx.ConnectError("lookup failed")
    exc.__cause__ = socket.gaierror(-2, "Name or service not known")
    r, _ = await run(always(exc))
    assert r.access is A.FAILED and r.error_category == "dns_failure"


async def test_ssl_error_not_retried():
    exc = httpx.ConnectError("handshake failed")
    exc.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
    r, calls = await run(always(exc))
    assert r.error_category == "ssl_error" and len(calls) == 1


async def test_invalid_html():
    r, _ = await run(always(page("just some plain text with no markup at all")))
    assert r.access is A.NEEDS_REVIEW and r.error_category == "invalid_html" and r.html is None


async def test_unexpected_responses():
    r, _ = await run(always(httpx.Response(200, json={"a": 1})))
    assert r.error_category == "unexpected_content_type" and r.access is A.NEEDS_REVIEW
    r, _ = await run(always(page("")))
    assert r.error_category == "empty_body"
    r, _ = await run(always(httpx.Response(418)))
    assert r.error_category == "unexpected_status" and r.access is A.NEEDS_REVIEW


async def test_soft_private_page_on_200():
    r, _ = await run(always(page("<html><body><h1>This profile is private</h1></body></html>")))
    assert r.access is A.PRIVATE_OR_INACCESSIBLE


async def test_marker_text_in_profile_content_is_not_a_false_positive():
    body = "<html><head><title>Jane</title></head><body><h1>Jane</h1><p>Page not found handling; profile is private mode</p></body></html>"
    r, _ = await run(always(page(body)))
    assert r.access is A.ACCESSIBLE


async def test_oversized_declared_and_streamed():
    r, _ = await run(always(page(OK_HTML + "x" * 500)), max_response_bytes=100)
    assert r.access is A.FAILED and r.error_category == "response_too_large" and r.html is None

    async def gen():
        yield b"<html>" + b"x" * 60
        yield b"x" * 60

    r, _ = await run(always(httpx.Response(200, content=gen(), headers={"content-type": "text/html"})),
                     max_response_bytes=100)
    assert r.error_category == "response_too_large"


@pytest.mark.parametrize("bad", [
    "http://www.skills.google/public_profiles/x",
    "https://evil.example/public_profiles/x",
    "https://www.skills.google/other/x",
])
async def test_invalid_url_makes_no_request(bad):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        with pytest.raises(InvalidProfileURLError):
            await ProfileFetcher(client=client).fetch(bad)
    assert calls == []


async def test_owned_client_is_reused_and_closed():
    f = ProfileFetcher(transport=httpx.MockTransport(always(page(OK_HTML))), sleep=_nosleep)
    r1, r2 = await f.fetch(URL), await f.fetch(URL)
    assert r1.access is r2.access is A.ACCESSIBLE
    client = f._client
    assert client is f._get_client()
    await f.aclose()
    await f.aclose()
    assert client.is_closed


@pytest.mark.parametrize("access,status", [
    (A.ACCESSIBLE, None),
    (A.PRIVATE_OR_INACCESSIBLE, "PRIVATE_INACCESSIBLE"),
    (A.NOT_FOUND, "NOT_FOUND"),
    (A.FAILED, "FAILED"),
    (A.NEEDS_REVIEW, "NEEDS_REVIEW"),
])
def test_verification_status_mapping(access, status):
    got = access.verification_status
    assert (got.value if got else None) == status


def test_config_from_settings():
    cfg = FetcherConfig.from_settings(Settings(request_timeout_seconds=7, max_retries=1))
    assert cfg.read_timeout == 7 and cfg.max_retries == 1