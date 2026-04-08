#!/usr/bin/env python3
"""Generate static HTML site from feed_cache.json."""

import json
import math
import random
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_tz
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from xml.sax.saxutils import escape

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
DISCOVERY_CONFIG_FILE = ROOT / "discovery_config.json"

# Default discovery algorithm configuration
DEFAULT_DISCOVERY_CONFIG = {
    "max_posts_per_site_daily": 3,  # Max posts per site in the feed per day
    "diversity_boost": 0.4,  # Boost factor for less active sites
    "time_decay_hours": 24,  # Hours for exponential time decay
    "randomization_factor": 0.2,  # How much randomness to inject
    "discovery_ratio": 0.0,  # Not used in new algorithm (current year priority)
    "max_posts_main_page": 100,  # Maximum posts to show on main page
}


def load_discovery_config():
    """Load discovery configuration from JSON file."""
    if DISCOVERY_CONFIG_FILE.exists():
        try:
            with open(DISCOVERY_CONFIG_FILE, encoding="utf-8") as f:
                config_data = json.load(f)
                return config_data.get("discovery", DEFAULT_DISCOVERY_CONFIG)
        except Exception as e:
            print(f"Warning: Could not load discovery config: {e}")
    return DEFAULT_DISCOVERY_CONFIG


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


def calculate_post_score(post, site_stats, now_ts, config):
    """Calculate discovery score for a post using hybrid algorithm."""
    
    # Time decay factor (newer posts get higher base score)
    post_ts = parse_date_ts(post.get("published", ""))
    # Don't fallback to current time - let strict parsing handle it
    
    # Get current year and post year for absolute preference
    from datetime import datetime
    current_year = datetime.now().year
    
    # More robust date parsing - be strict
    post_year = None
    try:
        if post_ts > 0:
            post_date = datetime.fromtimestamp(post_ts)
            post_year = post_date.year
        else:
            # Try to parse the original date string directly
            date_str = post.get("published", "")
            if date_str:
                # Try ISO format first
                if 'T' in date_str:
                    try:
                        post_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        post_year = post_date.year
                    except:
                        pass
                else:
                    # Try email format
                    from email.utils import parsedate_tz
                    import time
                    parsed = parsedate_tz(date_str)
                    if parsed:
                        post_date = datetime.fromtimestamp(time.mktime(parsed[:9]))
                        post_year = post_date.year
    except Exception as e:
        pass
    
    # If we can't parse the date, exclude the post
    if post_year is None:
        return 0.0
    
    # STRICT FILTERING: Exclude posts older than 2 years
    if post_year < current_year - 2:
        return 0.0  # Completely exclude old posts
    
    # Year-based multiplier with strict current year preference
    if post_year == current_year:
        year_multiplier = 100.0  # Massive boost for current year posts
    elif post_year == current_year - 1:
        year_multiplier = 1.0    # Normal score for last year
    elif post_year == current_year - 2:
        year_multiplier = 0.1    # Very low score for 2 years ago
    else:
        return 0.0  # Should not reach here due to filtering above
    
    # Standard time decay within the year
    hours_old = (now_ts - post_ts) / 3600
    time_decay = math.exp(-hours_old / config["time_decay_hours"])
    
    # Diversity boost (less active sites get higher scores)
    site_name = post["site_name"]
    site_post_count = site_stats.get(site_name, 1)
    diversity_factor = 1 + (config["diversity_boost"] / math.log(site_post_count + 1))
    
    # Randomization factor
    random_factor = 1 + (random.random() - 0.5) * config["randomization_factor"]
    
    # Combined score with year preference
    score = year_multiplier * time_decay * diversity_factor * random_factor
    
    return score


def apply_discovery_algorithm(all_posts):
    """Apply discovery algorithm to create a diverse, discovery-focused feed."""
    config = load_discovery_config()
    now_ts = datetime.now(timezone.utc).timestamp()
    current_year = datetime.now().year

    # Calculate site statistics
    site_stats = defaultdict(int)
    for post in all_posts:
        site_stats[post["site_name"]] += 1

    # Calculate scores for all posts and filter by age
    scored_posts = []
    for post in all_posts:
        score = calculate_post_score(post, site_stats, now_ts, config)
        if score > 0:  # Only include posts that pass the age filter
            post["discovery_score"] = score
            scored_posts.append(post)

    # Separate posts by year
    current_year_posts = []
    older_posts = []
    
    for post in scored_posts:
        # Parse post year - be strict about parsing
        post_year = None
        try:
            post_ts = parse_date_ts(post.get("published", ""))
            if post_ts > 0:
                post_date = datetime.fromtimestamp(post_ts)
                post_year = post_date.year
        except:
            pass
        
        # Only include posts with valid, parseable dates
        if post_year == current_year:
            current_year_posts.append(post)
        elif post_year is not None:  # Valid year but not current
            older_posts.append(post)
        # Posts with unparseable dates are excluded entirely

    # Sort each group by discovery score
    current_year_posts.sort(key=lambda p: p["discovery_score"], reverse=True)
    older_posts.sort(key=lambda p: p["discovery_score"], reverse=True)

    # Apply daily limits per site for current year posts first
    site_daily_count = defaultdict(int)
    final_current_year = []
    
    for post in current_year_posts:
        site_name = post["site_name"]
        if site_daily_count[site_name] < config["max_posts_per_site_daily"]:
            final_current_year.append(post)
            site_daily_count[site_name] += 1

    # Shuffle current year posts for randomness
    random.shuffle(final_current_year)
    
    # Mark current year posts as recent
    for post in final_current_year:
        post["is_discovery"] = False

    # Only add older posts if we need more content to reach max_posts
    max_posts = config.get("max_posts_main_page", 100)
    final_posts = final_current_year.copy()
    
    if len(final_posts) < max_posts:
        # Add older posts to fill remaining slots
        remaining_slots = max_posts - len(final_posts)
        
        # Reset site count for older posts (but respect the limit)
        older_filtered = []
        for post in older_posts:
            site_name = post["site_name"]
            if site_daily_count[site_name] < config["max_posts_per_site_daily"]:
                older_filtered.append(post)
                site_daily_count[site_name] += 1
                
                if len(older_filtered) >= remaining_slots:
                    break
        
        # Mark older posts as discovery
        for post in older_filtered:
            post["is_discovery"] = True
        
        # Shuffle older posts
        random.shuffle(older_filtered)
        
        # Add to final list
        final_posts.extend(older_filtered)

    return final_posts


def get_rss_posts(cache):
    """Get posts for RSS feed - chronological order, max 2 per blog."""
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
    
    # Filter out posts without valid dates and sort by date
    valid_posts = []
    for post in all_posts:
        post_ts = parse_date_ts(post.get("published", ""))
        if post_ts > 0:
            post["timestamp"] = post_ts
            valid_posts.append(post)
    
    # Sort by timestamp (newest first)
    valid_posts.sort(key=lambda p: p["timestamp"], reverse=True)
    
    # Apply max 2 posts per site limit
    site_count = defaultdict(int)
    rss_posts = []
    
    for post in valid_posts:
        site_name = post["site_name"]
        if site_count[site_name] < 2:  # Max 2 posts per site
            rss_posts.append(post)
            site_count[site_name] += 1
            
            # Limit total posts to 50 for RSS feed
            if len(rss_posts) >= 50:
                break
    
    return rss_posts


def get_all_posts(cache):
    """Get all posts and apply discovery algorithm."""
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
    
    # Add Arabic dates
    for post in all_posts:
        post["date_ar"] = to_arabic_date(post.get("published", ""))
    
    # Apply discovery algorithm
    discovery_posts = apply_discovery_algorithm(all_posts)
    
    return discovery_posts


def generate_rss_feed(cache):
    """Generate RSS feed XML."""
    rss_posts = get_rss_posts(cache)
    
    # RFC 2822 date format for RSS
    def format_rss_date(timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%a, %d %b %Y %H:%M:%S %z')
    
    # Build RSS XML
    rss_items = []
    for post in rss_posts:
        title = escape(post.get("title", ""))
        link = escape(post.get("link", ""))
        summary = escape(post.get("summary", "")[:500] + "..." if len(post.get("summary", "")) > 500 else post.get("summary", ""))
        site_name = escape(post.get("site_name", ""))
        pub_date = format_rss_date(post["timestamp"])
        
        item_xml = f"""    <item>
      <title>{title}</title>
      <link>{link}</link>
      <description>{summary}</description>
      <author>{site_name}</author>
      <pubDate>{pub_date}</pubDate>
      <guid>{link}</guid>
    </item>"""
        rss_items.append(item_xml)
    
    build_date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S %z')
    
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>الشبكة العربية الصغيرة</title>
    <link>https://arsweb.net/</link>
    <description>مجموعة منتقاة من المدونات والمواقع الشخصية باللغة العربية</description>
    <language>ar</language>
    <lastBuildDate>{build_date}</lastBuildDate>
    <atom:link href="https://arsweb.net/rss.xml" rel="self" type="application/rss+xml"/>
    <generator>Arabic Small Web</generator>
{chr(10).join(rss_items)}
  </channel>
</rss>"""
    
    return rss_xml


def render_site(cache):
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    all_posts = get_all_posts(cache)
    discovery_posts_count = sum(1 for post in all_posts if post.get("is_discovery", False))

    pages = {
        "index.html": "index.html",
        "about.html": "about.html",
    }

    for template_name, out_name in pages.items():
        context = {
            "cache": cache,
            "all_posts": all_posts,
            "discovery_posts_count": discovery_posts_count,
        }
        template = env.get_template(template_name)
        html = template.render(**context)
        (OUTPUT_DIR / out_name).write_text(html, encoding="utf-8")

    # Generate RSS feed
    rss_xml = generate_rss_feed(cache)
    (OUTPUT_DIR / "rss.xml").write_text(rss_xml, encoding="utf-8")

    if STATIC_DIR.exists():
        for item in STATIC_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, OUTPUT_DIR / item.name)
            elif item.is_dir():
                shutil.copytree(item, OUTPUT_DIR / item.name, dirs_exist_ok=True)


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
