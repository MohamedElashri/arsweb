"""HTTP helpers shared by feed fetching and validation."""

from urllib.parse import urlparse

import requests


DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; ArabicSmallWeb/1.0)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def origin_for_url(url):
    """Return the site origin for use as a same-site referer."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return None


def feed_request_headers(url=None, user_agent=DEFAULT_USER_AGENT, browser=False):
    """Build feed request headers, optionally using a browser-like profile."""
    if browser:
        headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
        referer = origin_for_url(url) if url else None
        if referer:
            headers["Referer"] = referer
        return headers

    return {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    }


def get_feed_response(url, timeout, user_agent=DEFAULT_USER_AGENT):
    """Fetch a feed, retrying 403 responses with browser-like headers."""
    response = requests.get(
        url,
        timeout=timeout,
        headers=feed_request_headers(url=url, user_agent=user_agent),
    )
    response.feed_retry_from_status_code = None
    if response.status_code != 403:
        return response

    retry_response = requests.get(
        url,
        timeout=timeout,
        headers=feed_request_headers(url=url, browser=True),
    )
    retry_response.feed_retry_from_status_code = response.status_code
    return retry_response


def was_retried_after_access_block(response):
    """Return True if this response came from a browser-header retry after 403."""
    return getattr(response, "feed_retry_from_status_code", None) == 403
