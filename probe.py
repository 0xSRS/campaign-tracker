import asyncio, sys
from urllib.parse import urlsplit
from scraper.fetcher import ProfileFetcher

async def main(url):
    f = ProfileFetcher()
    try:
        r = await f.fetch(url)
        print(r.http_status, r.access, r.error_category)
        if r.unfollowed_redirect:
            t = urlsplit(r.unfollowed_redirect)
            print("redirect target:", t.hostname, t.path)  # no query string
    finally:
        await f.aclose()

asyncio.run(main(sys.argv[1]))