#!/usr/bin/env python3
"""Generate static HTML site from feed_cache.json."""

import json
import shutil
from datetime import datetime, timezone
from email.utils import parsedate_tz
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
ARABIC_MONTHS = {
    "January": "يناير", "February": "فبراير", "March": "مارس",
    "April": "أبريل", "May": "مايو", "June": "يونيو",
    "July": "يوليو", "August": "أغسطس", "September": "سبتمبر",
    "October": "أكتوبر", "November": "نوفمبر", "December": "ديسمبر",
    "Jan": "يناير", "Feb": "فبراير", "Mar": "مارس",
    "Apr": "أبريل", "Jun": "يونيو", "Jul": "يوليو",
    "Aug": "أغسطس", "Sep": "سبتمبر", "Oct": "أكتوبر",
    "Nov": "نوفمبر", "Dec": "ديسمبر",
}

ROOT = Path(__file__).parent.parent
CACHE_FILE = ROOT / "feed_cache.json"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "public"
STATIC_DIR = ROOT / "static"


def load_cache():
    if not CACHE_FILE.exists():
        print("No cache file found. Run fetch_feeds.py first.")
        return None
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def to_arabic_date(date_str):
    if not date_str:
        return ""
    for en, ar in ARABIC_MONTHS.items():
        date_str = date_str.replace(en, ar)
    result = date_str.translate(ARABIC_DIGITS)
    parts = result.split()
    day = month = year = None
    for part in parts:
        if part.endswith(",") and len(part) <= 5:
            continue
        if part.startswith("+") or part.startswith("-"):
            break
        if part.isdigit() and len(part) <= 2 and day is None:
            day = part
        elif part in set(ARABIC_MONTHS.values()) and month is None:
            month = part
        elif len(part) == 4 and year is None:
            year = part
        elif part.isdigit() and ":" not in part and day is None:
            day = part
    if not day or not month:
        return ""
    current_year = str(datetime.now().year).translate(ARABIC_DIGITS)
    if year and year != current_year:
        return f"{day} {month} {year}"
    return f"{day} {month}"


def remove_em_dashes(text):
    if not text:
        return ""
    text = text.replace("—", " - ").replace("–", " - ")
    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
    text = text.replace("—", " - ").replace("–", " - ")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def parse_date_ts(date_str):
    try:
        tt = parsedate_tz(date_str)
        if tt:
            from calendar import timegm
            return timegm(tt)
    except Exception:
        pass
    return 0


def get_all_posts(cache):
    all_posts = []
    for site in cache["sites"]:
        for entry in site["entries"]:
            all_posts.append(
                {
                    **entry,
                    "site_name": site["name"],
                    "site_url": site["url"],
                    "summary": remove_em_dashes(entry.get("summary", "")),
                    "title": remove_em_dashes(entry.get("title", "")),
                }
            )
    all_posts.sort(key=lambda p: parse_date_ts(p.get("published", "")), reverse=True)
    for post in all_posts:
        post["date_ar"] = to_arabic_date(post.get("published", ""))
    return all_posts


def render_site(cache):
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    all_posts = get_all_posts(cache)

    pages = {
        "index.html": "index.html",
        "about.html": "about.html",
        "all-blogs.html": "all-blogs.html",
    }

    for template_name, out_name in pages.items():
        context = {
            "cache": cache,
            "all_posts": all_posts,
        }
        template = env.get_template(template_name)
        html = template.render(**context)
        (OUTPUT_DIR / out_name).write_text(html, encoding="utf-8")

    if STATIC_DIR.exists():
        for item in STATIC_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, OUTPUT_DIR / item.name)


def main():
    cache = load_cache()
    if cache is None:
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    render_site(cache)

    print(f"Site generated in {OUTPUT_DIR}/")
    print(f"  {cache['sites_count']} sites, {cache['posts_count']} posts")


if __name__ == "__main__":
    main()
