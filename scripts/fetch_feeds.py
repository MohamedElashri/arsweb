#!/usr/bin/env python3
"""Fetch and aggregate RSS feeds from sources.json."""

import html
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
SOURCES_FILE = ROOT / "sources.txt"
CACHE_FILE = ROOT / "feed_cache.json"
MAX_POSTS_PER_SITE = 5
MAX_POSTS_TOTAL = 500

ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def has_arabic(text):
    """Check if text contains Arabic characters."""
    return any("\u0600" <= c <= "\u06FF" or "\u0750" <= c <= "\u077F" for c in text)


def load_sources():
    """Load feed URLs from sources.txt (one URL per line)."""
    with open(SOURCES_FILE, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return urls


def fetch_feed(feed_url):
    """Fetch a single RSS feed and return parsed entries."""
    logger.info("Fetching %s ...", feed_url)
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        logger.error("Failed to fetch %s: %s", feed_url, e)
        return None

    if feed.bozo and not feed.entries:
        logger.warning("No entries from %s (bozo: %s)", feed_url, feed.bozo_exception)
        return None

    # Extract site name and URL from feed metadata
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

        title = html.unescape(entry.get("title", ""))
        summary = ""
        if hasattr(entry, "summary") and entry.summary:
            summary = re.sub(r"<[^>]+>", "", entry.summary)
            summary = html.unescape(summary)
            summary = summary[:500].strip()

        if not summary:
            continue
        if title and not has_arabic(title):
            continue

        entries.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
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
        "sites_count": len(sites),
        "posts_count": 0,
        "sites": [],
    }

    total_posts = 0
    for feed_url in sites:
        result = fetch_feed(feed_url)
        if result is None:
            logger.warning("Skipping %s due to errors", feed_url)
            continue

        cache["sites"].append(result)
        post_count = len(result["entries"])
        total_posts += post_count
        logger.info("Got %d entries from %s", post_count, result["name"])

        if total_posts >= MAX_POSTS_TOTAL:
            break

    cache["posts_count"] = total_posts

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    logger.info("Cache written to %s (%d sites, %d posts)", CACHE_FILE, cache["sites_count"], cache["posts_count"])


if __name__ == "__main__":
    main()
