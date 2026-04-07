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
SOURCES_FILE = ROOT / "sources.json"
CACHE_FILE = ROOT / "feed_cache.json"
MAX_POSTS_PER_SITE = 5
MAX_POSTS_TOTAL = 500

ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def has_arabic(text):
    """Check if text contains Arabic characters."""
    return any("\u0600" <= c <= "\u06FF" or "\u0750" <= c <= "\u077F" for c in text)


def load_sources():
    """Load the list of sites from sources.json."""
    with open(SOURCES_FILE, encoding="utf-8") as f:
        return json.load(f)["sites"]


def fetch_feed(site):
    """Fetch a single RSS feed and return parsed entries."""
    logger.info("Fetching %s ...", site["feed"])
    try:
        feed = feedparser.parse(site["feed"])
    except Exception as e:
        logger.error("Failed to fetch %s: %s", site["feed"], e)
        return None

    if feed.bozo and not feed.entries:
        logger.warning("No entries from %s (bozo: %s)", site["feed"], feed.bozo_exception)
        return None

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

        # Skip posts without summary
        if not summary:
            continue

        # Skip posts with no Arabic in title (allow mixed, but require some Arabic)
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
        "name": site["name"],
        "url": site["url"],
        "feed": site["feed"],
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
    for site in sites:
        result = fetch_feed(site)
        if result is None:
            logger.warning("Skipping %s due to errors", site.get("feed", "unknown"))
            continue

        cache["sites"].append(result)
        post_count = len(result["entries"])
        total_posts += post_count
        logger.info("Got %d entries from %s", post_count, site["name"])

        if total_posts >= MAX_POSTS_TOTAL:
            break

    cache["posts_count"] = total_posts

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    logger.info("Cache written to %s (%d sites, %d posts)", CACHE_FILE, cache["sites_count"], cache["posts_count"])


if __name__ == "__main__":
    main()
