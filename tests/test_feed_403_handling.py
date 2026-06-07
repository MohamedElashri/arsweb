import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import fetch_feeds
from scripts import validate_feeds
from scripts.feed_http import get_feed_response
from scripts.validate_feeds import FeedValidationResult, update_failure_state


class Feed403HandlingTests(unittest.TestCase):
    def test_feed_request_retries_403_with_browser_headers(self):
        first = Mock(status_code=403)
        second = Mock(status_code=200)

        with patch("scripts.feed_http.requests.get", side_effect=[first, second]) as get:
            response = get_feed_response("https://example.com/feed/", timeout=12)

        self.assertIs(response, second)
        self.assertEqual(get.call_count, 2)

        retry_headers = get.call_args_list[1].kwargs["headers"]
        self.assertTrue(retry_headers["User-Agent"].startswith("Mozilla/5.0 (Windows NT"))
        self.assertEqual(retry_headers["Referer"], "https://example.com/")
        self.assertEqual(response.feed_retry_from_status_code, 403)

    def test_403_then_html_challenge_is_access_blocked(self):
        response = Mock(status_code=200, content=b"<html><body>Forbidden</body></html>")
        response.raise_for_status = Mock()
        response.feed_retry_from_status_code = 403

        with patch.object(validate_feeds, "get_feed_response", return_value=response):
            result = validate_feeds.validate_feed_once("https://example.com/feed/", check_age=False)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "access_blocked")
        self.assertFalse(result.removable)
        self.assertFalse(result.remove_candidate)

    def test_access_blocked_failures_are_not_removal_candidates(self):
        state = {
            "feeds": {
                "https://blocked.example/feed/": {
                    "consecutive_failures": 5,
                    "last_reason": "http_error",
                    "last_details": "403 Client Error: Forbidden for url: https://blocked.example/feed/",
                },
                "https://broken.example/feed/": {
                    "consecutive_failures": 2,
                    "consecutive_removable_failures": 2,
                    "last_reason": "http_error",
                    "last_details": "500 Server Error",
                },
            }
        }

        blocked = FeedValidationResult(
            url="https://blocked.example/feed/",
            ok=False,
            reason="access_blocked",
            details="403 Client Error: Forbidden for url: https://blocked.example/feed/",
            removable=False,
            status_code=403,
        )
        broken = FeedValidationResult(
            url="https://broken.example/feed/",
            ok=False,
            reason="http_error",
            details="500 Server Error",
            temporary=True,
            status_code=500,
        )

        update_failure_state([blocked, broken], state, threshold=3)

        self.assertEqual(blocked.consecutive_failures, 6)
        self.assertEqual(blocked.consecutive_removable_failures, 0)
        self.assertFalse(blocked.remove_candidate)
        self.assertFalse(state["feeds"]["https://blocked.example/feed/"]["removable"])

        self.assertEqual(broken.consecutive_removable_failures, 3)
        self.assertTrue(broken.remove_candidate)

    def test_fetch_uses_cached_site_when_feed_is_still_403(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "feed_cache.json"
            previous_cache = {
                "generated_at": "2026-06-01T00:00:00+00:00",
                "sites": [
                    {
                        "name": "Cached Blog",
                        "url": "https://blocked.example/",
                        "feed": "https://blocked.example/feed/",
                        "entries": [
                            {
                                "title": "Cached title",
                                "summary": "Cached summary",
                                "link": "https://blocked.example/post",
                            }
                        ],
                        "error": None,
                    }
                ],
            }

            with (
                patch.object(fetch_feeds, "CACHE_FILE", cache_path),
                patch.object(fetch_feeds, "load_sources", return_value=["https://blocked.example/feed/"]),
                patch.object(fetch_feeds, "load_previous_cache", return_value=previous_cache),
                patch.object(
                    fetch_feeds,
                    "fetch_feed",
                    return_value=fetch_feeds.feed_failure(
                        "https://blocked.example/feed/",
                        "blocked",
                        reason="http_error",
                        status_code=403,
                    ),
                ),
            ):
                fetch_feeds.main()

            cache = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(cache["sites_count"], 1)
        self.assertEqual(cache["posts_count"], 1)
        self.assertEqual(cache["failed_feeds"], [])
        self.assertEqual(cache["stale_feeds"], ["https://blocked.example/feed/"])
        self.assertTrue(cache["sites"][0]["stale"])
        self.assertEqual(cache["sites"][0]["stale_reason"], "access_blocked")


if __name__ == "__main__":
    unittest.main()
