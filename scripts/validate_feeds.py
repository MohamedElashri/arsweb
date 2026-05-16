#!/usr/bin/env python3
"""Validate RSS feeds for quality and recency."""

import argparse
import json
import sys
from calendar import timegm
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_tz
from pathlib import Path

import feedparser
import requests


USER_AGENT = "Mozilla/5.0 (compatible; ASW-Validator/1.0)"


@dataclass
class FeedValidationResult:
    url: str
    ok: bool
    reason: str
    details: str = ""
    status_code: int | None = None
    entries: int = 0
    title: str = ""
    latest_date: str = ""


def load_urls(path):
    """Load feed URLs from a text file."""
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def parse_entry_date(entry):
    """Parse the best available published/updated date from a feed entry."""
    for attr in ("published", "updated"):
        value = getattr(entry, attr, "")
        if not value:
            continue
        parsed = parsedate_tz(value)
        if parsed:
            return datetime.fromtimestamp(timegm(parsed[:9]), timezone.utc)
    return None


def print_result(result):
    """Print a human-readable validation result."""
    print(f"🔍 Validating: {result.url}")
    if result.status_code:
        print(f"  ✅ HTTP Status: {result.status_code}")
    if result.entries:
        print(f"  ✅ Found {result.entries} entries")
    if result.latest_date:
        print(f"  ✅ Latest post date: {result.latest_date}")
    if result.title:
        print(f"  📝 Feed title: {result.title}")

    if result.ok:
        if result.details:
            print(f"  ⚠️  {result.details}")
        else:
            print("  ✅ Feed validation passed")
        return

    print(f"  ❌ {result.reason}: {result.details}")


def validate_feed(url, check_age=True, timeout=30, verbose=True):
    """Validate a single RSS feed."""
    result = FeedValidationResult(url=url, ok=False, reason="unknown")

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        result.status_code = response.status_code
    except Exception as e:
        result.reason = "http_error"
        result.details = str(e)
        if verbose:
            print_result(result)
        return result

    try:
        feed = feedparser.parse(response.content)
    except Exception as e:
        result.reason = "parse_error"
        result.details = str(e)
        if verbose:
            print_result(result)
        return result

    result.entries = len(feed.entries)
    result.title = getattr(feed.feed, "title", "")

    if feed.bozo and not feed.entries:
        result.reason = "parse_error"
        result.details = str(feed.bozo_exception)
        if verbose:
            print_result(result)
        return result

    if not feed.entries:
        result.reason = "empty_feed"
        result.details = "No entries found in feed"
        if verbose:
            print_result(result)
        return result

    if check_age:
        current_time = datetime.now(timezone.utc)
        two_years_ago = current_time.replace(year=current_time.year - 2)
        entry_date = parse_entry_date(feed.entries[0])

        if entry_date:
            result.latest_date = entry_date.strftime("%Y-%m-%d")
            if entry_date < two_years_ago:
                result.reason = "stale_feed"
                result.details = f"Latest post is older than 2 years: {result.latest_date}"
                if verbose:
                    print_result(result)
                return result
        else:
            result.details = "Could not parse entry date, but feed is accessible"

    if feed.bozo:
        result.details = f"Feed has parsing issues: {feed.bozo_exception}"

    result.ok = True
    result.reason = "ok"
    if verbose:
        print_result(result)
    return result


def write_report(path, results):
    """Write machine-readable validation output."""
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "failed": sum(1 for result in results if not result.ok),
        "results": [asdict(result) for result in results],
    }
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_failed_feeds(sources_file, failed_urls):
    """Remove failed feed URLs from sources.txt while preserving comments and spacing."""
    failed_urls = set(failed_urls)
    path = Path(sources_file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept_lines = []
    removed = []

    for line in lines:
        stripped = line.strip()
        if stripped in failed_urls:
            removed.append(stripped)
            continue
        kept_lines.append(line)

    if removed:
        path.write_text("".join(kept_lines), encoding="utf-8")

    return removed


def summarize(results):
    failed = [result for result in results if not result.ok]
    print("=" * 60)
    if failed:
        print(f"❌ {len(failed)} feed(s) failed validation:")
        for result in failed:
            print(f"  - {result.url} ({result.reason}: {result.details})")
    else:
        print(f"✅ All {len(results)} feed(s) passed validation!")
    return failed


def main():
    """Main validation function."""
    parser = argparse.ArgumentParser(description="Validate RSS feeds")
    parser.add_argument("--url", help="Validate a single URL")
    parser.add_argument("--file", default="sources.txt", help="File containing URLs to validate")
    parser.add_argument("--no-age-check", action="store_true", help="Skip age validation")
    parser.add_argument("--max-feeds", type=int, help="Maximum number of feeds to validate")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--report", help="Write JSON validation report to this path")
    parser.add_argument("--remove-failed", action="store_true", help="Remove failed URLs from the source file")
    parser.add_argument("--soft-fail", action="store_true", help="Report failures but exit successfully")

    args = parser.parse_args()
    check_age = not args.no_age_check

    if args.url:
        print("📋 Validating single feed...")
        print("=" * 60)
        result = validate_feed(args.url, check_age, args.timeout)
        print("=" * 60)
        if args.report:
            write_report(args.report, [result])
        if result.ok:
            print("✅ Feed validation passed!")
            sys.exit(0)
        print("❌ Feed validation failed!")
        sys.exit(0 if args.soft_fail else 1)

    sources_file = Path(args.file)
    if not sources_file.exists():
        print(f"❌ File not found: {args.file}")
        sys.exit(1)

    urls = load_urls(sources_file)
    if args.max_feeds:
        urls = urls[: args.max_feeds]

    print(f"📋 Validating {len(urls)} feed(s) from {args.file}...")
    print("=" * 60)

    results = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        results.append(validate_feed(url, check_age, args.timeout))
        print()

    failed = summarize(results)

    if args.report:
        write_report(args.report, results)
        print(f"🧾 Wrote report to {args.report}")

    if args.remove_failed and failed:
        removed = remove_failed_feeds(sources_file, [result.url for result in failed])
        print(f"🧹 Removed {len(removed)} failed feed(s) from {args.file}")

    if failed and not (args.soft_fail or args.remove_failed):
        sys.exit(1)


if __name__ == "__main__":
    main()
