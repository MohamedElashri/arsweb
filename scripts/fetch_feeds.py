#!/usr/bin/env python3
"""Fetch and aggregate RSS feeds from sources.txt."""

import html
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
CONTENT_WORKERS = 4

ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "em", "figcaption", "figure", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "img", "li", "mark", "ol", "p", "pre", "strong",
    "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
}
VOID_TAGS = {"br", "hr", "img"}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "loading"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
ALLOWED_SCHEMES = {"http", "https", "mailto"}


def has_arabic(text):
    """Check if text contains Arabic characters."""
    return any("\u0600" <= c <= "\u06FF" or "\u0750" <= c <= "\u077F" for c in text)


def load_sources():
    """Load feed URLs from sources.txt (one URL per line)."""
    seen = set()
    urls = []
    with open(SOURCES_FILE, encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith("#") or url in seen:
                continue
            seen.add(url)
            urls.append(url)
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
    parsed = urlparse(url)
    if parsed.scheme in ALLOWED_SCHEMES:
        return url
    return None


class ArticleHTMLSanitizer(HTMLParser):
    """Small allowlist sanitizer for extracted article HTML."""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts = []
        self.open_tags = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form"}:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return

        clean_attrs = self.clean_attrs(tag, attrs)
        if tag == "img" and not any(name == "src" for name, _ in clean_attrs):
            return
        attr_text = "".join(
            f' {name}="{html.escape(value, quote=True)}"'
            for name, value in clean_attrs
        )
        self.parts.append(f"<{tag}{attr_text}>")
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self.open_tags:
            while self.open_tags:
                open_tag = self.open_tags.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(html.escape(data, quote=False))

    def clean_attrs(self, tag, attrs):
        allowed = ALLOWED_ATTRS.get(tag, set())
        clean = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed or value is None:
                continue
            if name in {"href", "src"}:
                value = clean_url(html.unescape(value), self.base_url)
                if not value:
                    continue
            elif name in {"colspan", "rowspan"}:
                value = value.strip()
                if not value.isdigit() or not 1 <= int(value) <= 20:
                    continue
            else:
                value = normalize_text(html.unescape(value))[:300]
            clean.append((name, value))

        if tag == "a" and any(name == "href" for name, _ in clean):
            clean.append(("target", "_blank"))
            clean.append(("rel", "noopener noreferrer"))
        if tag == "img":
            if not any(name == "src" for name, _ in clean):
                return []
            if not any(name == "alt" for name, _ in clean):
                clean.append(("alt", ""))
            clean = [(name, value) for name, value in clean if name != "loading"]
            clean.append(("loading", "lazy"))
        return clean

    def get_html(self):
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


def sanitize_html(html_str, source_url):
    """Clean and sanitize trafilatura output HTML."""
    html_str = normalize_text(html_str)
    if not html_str or len(html_str) < 200:
        return None

    # Strip trafilatura's <html><body> wrapper
    html_str = re.sub(r"^\s*<html>\s*<body>\s*", "", html_str, flags=re.IGNORECASE)
    html_str = re.sub(r"\s*</body>\s*</html>\s*$", "", html_str, flags=re.IGNORECASE)
    html_str = html_str.strip()

    # Convert trafilatura <graphic> to browser-friendly <img>
    html_str = re.sub(
        r'<graphic\s+src="([^"]*)"\s*/?>',
        r'<img src="\1" loading="lazy" alt="" />',
        html_str,
    )

    sanitizer = ArticleHTMLSanitizer(source_url)
    sanitizer.feed(html_str)
    html_str = sanitizer.get_html()

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

        entries.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
                "content": None,
            }
        )

    extract_article_contents(entries)

    return {
        "name": site_name,
        "url": site_url,
        "feed": feed_url,
        "entries": entries,
        "error": None,
    }


def extract_article_contents(entries):
    """Fetch article contents for feed entries with bounded concurrency."""
    link_indexes = [
        (index, entry["link"])
        for index, entry in enumerate(entries)
        if entry.get("link")
    ]
    if not link_indexes:
        return

    with ThreadPoolExecutor(max_workers=CONTENT_WORKERS) as executor:
        future_to_index = {
            executor.submit(extract_article_content, link): index
            for index, link in link_indexes
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                entries[index]["content"] = future.result()
            except Exception as e:
                logger.debug("Article extraction failed for %s: %s", entries[index]["link"], e)


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
