#!/usr/bin/env python3
"""Fetch and aggregate RSS feeds from sources.json."""

import html
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
import trafilatura

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
SOURCES_FILE = ROOT / "sources.txt"
CACHE_FILE = ROOT / "feed_cache.json"
MAX_POSTS_PER_SITE = 10
MAX_POSTS_TOTAL = 800
REQUEST_TIMEOUT = 12
CONTENT_TIMEOUT = 15
MAX_CONTENT_LENGTH = 40000  # characters

ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def has_arabic(text):
    """Check if text contains Arabic characters."""
    return any("\u0600" <= c <= "\u06FF" or "\u0750" <= c <= "\u077F" for c in text)


def load_sources():
    """Load feed URLs from sources.txt (one URL per line)."""
    with open(SOURCES_FILE, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return urls


def normalize_text(text):
    """Normalize text decoded from Arabic pages and fix common mojibake."""
    if not text:
        return ""

    if not has_arabic(text) and any(marker in text for marker in ("Ø", "Ù", "Ú", "Ã")):
        try:
            fixed = text.encode("latin1").decode("utf-8")
            if has_arabic(fixed):
                text = fixed
        except UnicodeError:
            pass

    text = unicodedata.normalize("NFC", text)
    text = text.replace("\ufeff", "").replace("\xa0", " ")
    text = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text.strip()


def decode_response(resp):
    """Decode a response with a better default for Arabic sites."""
    encoding = resp.encoding
    if not encoding or encoding.lower() in {"iso-8859-1", "ascii"}:
        encoding = resp.apparent_encoding
    if encoding:
        resp.encoding = encoding
    return normalize_text(resp.text)


def clean_url(url, base_url=None):
    """Only allow http/https/mailto URLs."""
    if not url:
        return None
    url = url.strip()
    if base_url:
        url = urljoin(base_url, url)
    if url.startswith("http://") or url.startswith("https://") or url.startswith("mailto:"):
        return url
    return None


def sanitize_html(html_str, source_url):
    """Clean and sanitize trafilatura output HTML."""
    html_str = normalize_text(html_str)
    if not html_str or len(html_str) < 200:
        return None

    # Strip trafilatura's <html><body> wrapper
    html_str = re.sub(r"^\s*<html>\s*<body>\s*", "", html_str, flags=re.IGNORECASE)
    html_str = re.sub(r"\s*</body>\s*</html>\s*$", "", html_str, flags=re.IGNORECASE)
    html_str = html_str.strip()

    # Trafilatura already strips scripts/styles/nav/etc.
    # We just need to validate URLs and truncate.
    def clean_attr(match):
        attr = match.group(1)
        val = match.group(2)
        if attr in ("href", "src"):
            cleaned = clean_url(html.unescape(val), source_url)
            if cleaned:
                return f'{attr}="{html.escape(cleaned, quote=True)}"'
            return ''
        return match.group(0)

    # Convert trafilatura <graphic> to browser-friendly <img>
    html_str = re.sub(
        r'<graphic\s+src="([^"]*)"\s*/?>',
        r'<img src="\1" loading="lazy" alt="" />',
        html_str,
    )

    # Clean href/src attributes
    html_str = re.sub(r'(href|src)="([^"]*)"', clean_attr, html_str)
    html_str = re.sub(r"(href|src)='([^']*)'", clean_attr, html_str)

    # Collapse excessive whitespace without disturbing paragraph boundaries.
    html_str = re.sub(r"\n\s*\n", "\n\n", html_str)
    html_str = html_str.strip()

    # Truncate if too long (try to end at a paragraph boundary)
    if len(html_str) > MAX_CONTENT_LENGTH:
        cutoff = html_str[:MAX_CONTENT_LENGTH]
        last_p = cutoff.rfind("</p>")
        if last_p > MAX_CONTENT_LENGTH * 0.7:
            html_str = cutoff[:last_p + 4]
        else:
            html_str = cutoff

    if len(html_str) < 200:
        return None

    return html_str


def extract_article_content(url):
    """Fetch article HTML and extract clean content using trafilatura."""
    try:
        resp = requests.get(
            url,
            timeout=CONTENT_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            },
        )
        resp.raise_for_status()
    except Exception as e:
        logger.debug("Failed to fetch article %s: %s", url, e)
        return None

    content_type = resp.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        return None

    try:
        page_html = decode_response(resp)
        result = trafilatura.extract(
            page_html,
            include_comments=False,
            include_tables=False,
            include_images=True,
            include_formatting=True,
            include_links=True,
            favor_recall=True,
            target_language="ar",
            url=resp.url or url,
            output_format="html",
        )
    except Exception as e:
        logger.debug("Trafilatura failed for %s: %s", url, e)
        return None

    return sanitize_html(result, resp.url or url)


def fetch_feed(feed_url):
    """Fetch a single RSS feed and return parsed entries."""
    logger.info("Fetching %s ...", feed_url)
    try:
        resp = requests.get(
            feed_url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ArabicSmallWeb/1.0)",
                "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.8",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            },
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        logger.error("Failed to fetch %s: %s", feed_url, e)
        return None

    if feed.bozo and not feed.entries:
        logger.warning("No entries from %s (bozo: %s)", feed_url, feed.bozo_exception)
        return None

    site_name = feed.feed.get("title", "").strip() or feed_url
    site_url = feed.feed.get("link", "").strip() or feed_url

    entries = []
    for entry in feed.entries[:MAX_POSTS_PER_SITE]:
        published = ""
        if hasattr(entry, "published") and entry.published:
            published = entry.published
        elif hasattr(entry, "updated") and entry.updated:
            published = entry.updated

        link = ""
        if hasattr(entry, "link") and entry.link:
            link = entry.link

        title = normalize_text(html.unescape(entry.get("title", "")))
        summary = ""
        if hasattr(entry, "summary") and entry.summary:
            summary = re.sub(r"<[^>]+>", " ", entry.summary)
            summary = normalize_text(html.unescape(summary))
            summary = summary[:1500].strip()

        if not summary:
            continue
        if title and not has_arabic(title):
            continue

        # Extract full article content
        content = None
        if link:
            logger.debug("Extracting content from %s", link)
            content = extract_article_content(link)

        entries.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
                "content": content,
            }
        )

    return {
        "name": site_name,
        "url": site_url,
        "feed": feed_url,
        "entries": entries,
        "error": None,
    }


def main():
    sites = load_sources()
    cache = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources_count": len(sites),
        "sites_count": 0,
        "posts_count": 0,
        "sites": [],
        "failed_feeds": [],
    }

    total_posts = 0
    for feed_url in sites:
        result = fetch_feed(feed_url)
        if result is None:
            logger.warning("Skipping %s due to errors", feed_url)
            cache["failed_feeds"].append(feed_url)
            continue

        cache["sites"].append(result)
        post_count = len(result["entries"])
        total_posts += post_count
        logger.info("Got %d entries from %s", post_count, result["name"])

        if total_posts >= MAX_POSTS_TOTAL:
            break

    cache["posts_count"] = total_posts
    cache["sites_count"] = len(cache["sites"])

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    logger.info(
        "Cache written to %s (%d/%d sites, %d posts, %d failed feeds)",
        CACHE_FILE,
        cache["sites_count"],
        cache["sources_count"],
        cache["posts_count"],
        len(cache["failed_feeds"]),
    )


if __name__ == "__main__":
    main()
