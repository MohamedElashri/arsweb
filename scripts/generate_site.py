#!/usr/bin/env python3
"""Generate static HTML site from feed_cache.json."""

import json
import shutil
from datetime import datetime, timezone
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
TRANSLATIONS_FILE = ROOT / "translations.json"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "public"
STATIC_DIR = ROOT / "static"


def load_cache():
    """Load the feed cache."""
    if not CACHE_FILE.exists():
        print("No cache file found. Run fetch_feeds.py first.")
        return None
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_translations():
    """Load translations."""
    with open(TRANSLATIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def to_arabic_date(date_str):
    """Convert an English date string to Arabic: DD شهر or DD شهر YYYY if not current year."""
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
    """Remove em dashes from text, replacing with suitable alternatives."""
    if not text:
        return ""
    text = text.replace("—", " - ").replace("–", " - ")
    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
    text = text.replace("—", " - ").replace("–", " - ")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def get_all_posts(cache):
    """Flatten all posts from all sites, sorted chronologically."""
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

    # Sort by published date (newest first)
    all_posts.sort(key=lambda p: p.get("published", ""), reverse=True)
    return all_posts


def render_site(cache, translations):
    """Render the full static site."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    env.globals["now"] = lambda: datetime.now(timezone.utc).isoformat()
    env.filters["emdash"] = remove_em_dashes

    all_posts = get_all_posts(cache)

    # Add Arabic-formatted dates to posts
    for post in all_posts:
        post["date_ar"] = to_arabic_date(post.get("published", ""))

    # Render index.html (AR default) and index-en.html
    for lang in ("ar", "en"):
        t = translations[lang]
        context = {
            "lang": lang,
            "dir": "rtl" if lang == "ar" else "ltr",
            "t": t,
            "cache": cache,
            "all_posts": all_posts,
            "is_index": True,
        }
        template = env.get_template("index.html")
        html = template.render(**context)

        if lang == "en":
            out_file = OUTPUT_DIR / "index-en.html"
        else:
            out_file = OUTPUT_DIR / "index.html"
        out_file.write_text(html, encoding="utf-8")

    # Render about page (AR default and EN)
    for lang in ("ar", "en"):
        t = translations[lang]
        context = {
            "lang": lang,
            "dir": "rtl" if lang == "ar" else "ltr",
            "t": t,
            "cache": cache,
        }
        template = env.get_template("about.html")
        html = template.render(**context)

        if lang == "en":
            out_file = OUTPUT_DIR / "about-en.html"
        else:
            out_file = OUTPUT_DIR / "about.html"
        out_file.write_text(html, encoding="utf-8")

    # Render all-blogs page (AR default and EN)
    for lang in ("ar", "en"):
        t = translations[lang]
        context = {
            "lang": lang,
            "dir": "rtl" if lang == "ar" else "ltr",
            "t": t,
            "cache": cache,
        }
        template = env.get_template("all-blogs.html")
        html = template.render(**context)

        if lang == "en":
            out_file = OUTPUT_DIR / "all-blogs-en.html"
        else:
            out_file = OUTPUT_DIR / "all-blogs.html"
        out_file.write_text(html, encoding="utf-8")

    # Copy static files
    if STATIC_DIR.exists():
        for item in STATIC_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, OUTPUT_DIR / item.name)


def main():
    cache = load_cache()
    if cache is None:
        return

    translations = load_translations()

    OUTPUT_DIR.mkdir(exist_ok=True)
    render_site(cache, translations)

    print(f"Site generated in {OUTPUT_DIR}/")
    print(f"  {cache['sites_count']} sites, {cache['posts_count']} posts")


if __name__ == "__main__":
    main()
