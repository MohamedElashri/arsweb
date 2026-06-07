#!/usr/bin/env python3
"""Validate RSS feeds for quality and recency."""

import argparse
import json
import sys
import time
from calendar import timegm
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_tz
from pathlib import Path

import feedparser
import requests

try:
    from feed_http import get_feed_response, was_retried_after_access_block
except ImportError:
    from scripts.feed_http import get_feed_response, was_retried_after_access_block

USER_AGENT = "Mozilla/5.0 (compatible; ASW-Validator/1.0)"


@dataclass
class FeedValidationResult:
    url: str
    ok: bool
    reason: str
    details: str = ""
    temporary: bool = False
    removable: bool = True
    consecutive_failures: int = 0
    consecutive_removable_failures: int = 0
    remove_candidate: bool = False
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


def is_temporary_http_failure(status_code):
    """Classify status codes that are likely to recover without source removal."""
    return status_code in {408, 425, 429} or 500 <= status_code <= 599


def is_access_blocked(status_code):
    """Classify HTTP statuses that mean the runner is blocked, not the feed is dead."""
    return status_code == 403


def mark_access_blocked(result, details):
    """Mark a validation result as blocked by the host instead of removable."""
    result.reason = "access_blocked"
    result.details = details
    result.removable = False
    return result


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

    temporary = "temporary " if result.temporary else ""
    removal_note = " (not a removal candidate)" if not result.removable else ""
    print(f"  ❌ {temporary}{result.reason}: {result.details}{removal_note}")


def validate_feed_once(url, check_age=True, timeout=30):
    """Validate a single RSS feed."""
    result = FeedValidationResult(url=url, ok=False, reason="unknown")

    try:
        response = get_feed_response(url, timeout=timeout, user_agent=USER_AGENT)
        response.raise_for_status()
        result.status_code = response.status_code
    except requests.HTTPError as e:
        result.status_code = e.response.status_code if e.response is not None else None
        if result.status_code and is_access_blocked(result.status_code):
            result.reason = "access_blocked"
            result.removable = False
        else:
            result.reason = "http_error"
        result.details = str(e)
        result.temporary = bool(result.status_code and is_temporary_http_failure(result.status_code))
        return result
    except requests.RequestException as e:
        result.reason = "http_error"
        result.details = str(e)
        result.temporary = True
        return result

    try:
        feed = feedparser.parse(response.content)
    except Exception as e:
        result.reason = "parse_error"
        result.details = str(e)
        return result

    result.entries = len(feed.entries)
    result.title = getattr(feed.feed, "title", "")

    if feed.bozo and not feed.entries:
        if was_retried_after_access_block(response):
            return mark_access_blocked(
                result,
                f"Unreadable feed response after 403 browser-header retry: {feed.bozo_exception}",
            )
        result.reason = "parse_error"
        result.details = str(feed.bozo_exception)
        return result

    if not feed.entries:
        if was_retried_after_access_block(response):
            return mark_access_blocked(
                result,
                "No feed entries found after 403 browser-header retry",
            )
        result.reason = "empty_feed"
        result.details = "No entries found in feed"
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
                return result
        else:
            result.details = "Could not parse entry date, but feed is accessible"

    if feed.bozo:
        result.details = f"Feed has parsing issues: {feed.bozo_exception}"

    result.ok = True
    result.reason = "ok"
    return result


def validate_feed(url, check_age=True, timeout=30, verbose=True, retries=1, retry_delay=2):
    """Validate a single RSS feed with retry for temporary failures."""
    result = None
    attempts = max(1, retries + 1)
    for attempt in range(1, attempts + 1):
        result = validate_feed_once(url, check_age, timeout)
        if result.ok or not result.temporary or attempt == attempts:
            break
        time.sleep(retry_delay)

    if verbose:
        print_result(result)
    return result


def load_failure_state(path):
    """Load previous feed validation state."""
    if not path or not Path(path).exists():
        return {"feeds": {}}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"feeds": {}}


def previous_removable_failures(feed_state):
    """Return the previous removal-eligible failure count, migrating older state."""
    if "consecutive_removable_failures" in feed_state:
        return int(feed_state.get("consecutive_removable_failures", 0))

    details = str(feed_state.get("last_details", ""))
    if feed_state.get("last_reason") == "access_blocked" or "403" in details or "Forbidden" in details:
        return 0

    return int(feed_state.get("consecutive_failures", 0))


def update_failure_state(results, state, threshold):
    """Update consecutive failure counts and mark removal candidates."""
    feeds = state.setdefault("feeds", {})
    now = datetime.now(timezone.utc).isoformat()
    active_urls = {result.url for result in results}

    for url in list(feeds):
        if url not in active_urls:
            feeds.pop(url, None)

    for result in results:
        feed_state = feeds.setdefault(result.url, {})
        if result.ok:
            feed_state["consecutive_failures"] = 0
            feed_state["consecutive_removable_failures"] = 0
            feed_state["last_reason"] = "ok"
            feed_state["removable"] = True
            result.consecutive_failures = 0
            result.consecutive_removable_failures = 0
            result.remove_candidate = False
            continue

        consecutive_failures = int(feed_state.get("consecutive_failures", 0)) + 1
        consecutive_removable_failures = (
            previous_removable_failures(feed_state) + 1 if result.removable else 0
        )
        feed_state.update(
            {
                "consecutive_failures": consecutive_failures,
                "consecutive_removable_failures": consecutive_removable_failures,
                "last_reason": result.reason,
                "last_details": result.details,
                "temporary": result.temporary,
                "removable": result.removable,
                "status_code": result.status_code,
                "last_failed_at": now,
            }
        )
        result.consecutive_failures = consecutive_failures
        result.consecutive_removable_failures = consecutive_removable_failures
        result.remove_candidate = result.removable and consecutive_removable_failures >= threshold

    state["updated_at"] = now
    return state


def save_failure_state(path, state):
    """Persist feed validation state."""
    if path:
        Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(path, results):
    """Write machine-readable validation output."""
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "failed": sum(1 for result in results if not result.ok),
        "remove_candidates": sum(1 for result in results if result.remove_candidate),
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
    parser.add_argument("--retries", type=int, default=1, help="Retry count for temporary failures")
    parser.add_argument("--report", help="Write JSON validation report to this path")
    parser.add_argument("--failure-state", help="Read/write JSON state for consecutive failures")
    parser.add_argument("--failure-threshold", type=int, default=1, help="Consecutive failures before removal")
    parser.add_argument("--remove-failed", action="store_true", help="Remove failed URLs from the source file")
    parser.add_argument("--soft-fail", action="store_true", help="Report failures but exit successfully")

    args = parser.parse_args()
    check_age = not args.no_age_check

    if args.url:
        print("📋 Validating single feed...")
        print("=" * 60)
        result = validate_feed(args.url, check_age, args.timeout, retries=args.retries)
        state = load_failure_state(args.failure_state)
        update_failure_state([result], state, args.failure_threshold)
        save_failure_state(args.failure_state, state)
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
        results.append(validate_feed(url, check_age, args.timeout, retries=args.retries))
        print()

    state = load_failure_state(args.failure_state)
    update_failure_state(results, state, args.failure_threshold)
    save_failure_state(args.failure_state, state)

    failed = summarize(results)

    if args.report:
        write_report(args.report, results)
        print(f"🧾 Wrote report to {args.report}")

    if args.remove_failed and failed:
        candidates = [result.url for result in failed if result.remove_candidate]
        removed = remove_failed_feeds(sources_file, candidates)
        print(
            f"🧹 Removed {len(removed)} feed(s) with at least "
            f"{args.failure_threshold} consecutive failure(s) from {args.file}"
        )

    if failed and not (args.soft_fail or args.remove_failed):
        sys.exit(1)


if __name__ == "__main__":
    main()
