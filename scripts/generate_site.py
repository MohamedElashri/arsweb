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
    "randomization_factor": 0.1,  # How much randomness to inject
    "discovery_ratio": 0.1,  # Ratio of older/diverse posts vs recent posts
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
    if post_ts == 0:
        post_ts = now_ts  # Fallback for posts without dates
    
    # Get current year and post year for absolute preference
    from datetime import datetime
    current_year = datetime.now().year
    
    # More robust date parsing
    post_year = current_year  # Default to current year if parsing fails
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
        # If all parsing fails, default to current year to give benefit of doubt
        post_year = current_year
    
    # Year-based multiplier (absolute preference for current year)
    if post_year == current_year:
        year_multiplier = 10.0  # 10x boost for current year posts
    elif post_year == current_year - 1:
        year_multiplier = 3.0   # 3x boost for last year posts
    elif post_year >= current_year - 2:
        year_multiplier = 1.5   # 1.5x boost for posts within 2 years
    else:
        year_multiplier = 0.1   # Heavily penalize older posts
    
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
    
    # Calculate site statistics
    site_stats = defaultdict(int)
    for post in all_posts:
        site_stats[post["site_name"]] += 1
    
    # Calculate scores for all posts
    for post in all_posts:
        post["discovery_score"] = calculate_post_score(post, site_stats, now_ts, config)
    
    # Sort by discovery score
    all_posts.sort(key=lambda p: p["discovery_score"], reverse=True)
    
    # Apply daily limits per site to prevent dominance
    site_daily_count = defaultdict(int)
    filtered_posts = []
    
    for post in all_posts:
        site_name = post["site_name"]
        if site_daily_count[site_name] < config["max_posts_per_site_daily"]:
            filtered_posts.append(post)
            site_daily_count[site_name] += 1
    
    # Limit total posts for main page
    max_posts = config.get("max_posts_main_page", 24)
    filtered_posts = filtered_posts[:max_posts]
    
    # Split into recent and discovery sections
    total_posts = len(filtered_posts)
    discovery_count = int(total_posts * config["discovery_ratio"])
    recent_count = total_posts - discovery_count
    
    # Recent posts (higher discovery scores)
    recent_posts = filtered_posts[:recent_count]
    for post in recent_posts:
        post["is_discovery"] = False
    
    # Discovery posts (mix of older and diverse content)
    discovery_posts = filtered_posts[recent_count:recent_count + discovery_count]
    for post in discovery_posts:
        post["is_discovery"] = True
    
    # Shuffle discovery posts for more randomness
    random.shuffle(discovery_posts)
    
    # Combine and return
    final_posts = recent_posts + discovery_posts
    
    return final_posts


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
